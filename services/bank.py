"""Economy core: deposits, loans and the global Corporation account.

Money rules (kept deliberately simple but internally consistent):

* Deposit principal is **moved** out of the player's liquid ``size`` (it freezes:
  hidden from /top, unusable in duels, no /dick growth). Interest is paid **by the
  Corporation** (its balance shrinks) and accrues only on days the owner actually
  plays /dick. The effective rate decays per active day and total yield is capped,
  so "deposit and forget" never pays off. Early withdrawal forfeits accrued
  interest and pays a penalty to the Corporation; deposits can also be randomly
  (partially) confiscated by the Corporation.
* Loan principal is **minted** into liquid ``size`` immediately and burned on
  repayment (net-zero). Interest grows the debt by calendar time and, when repaid
  or garnished, becomes Corporation income. Past the due date the loan defaults and
  is recovered by garnishing /dick gains and duel winnings.
* The Corporation is the single global house account. It may go negative — that is
  the bankruptcy state, shown (with crude flavour) in /corp.

The pure helpers (rates, limits, penalties) take a config snapshot and are unit
tested; the async ops below wrap them with repository IO. Callers that already hold
the per-chat lock (the /dick and /duel handlers) use the ``*_on_dict`` helpers so we
never fight their in-memory player dict.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from repositories import bank as repo
from repositories import events as E
from repositories import players as players_repo
from services import cooldown
from services.global_settings import GlobalConfig, get_config_sync

_LOAN_DENY_KEY = "loan_denied"

DAY = 86400


def _now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- #
# Pure helpers (no IO) — unit tested.
# --------------------------------------------------------------------------- #


def effective_deposit_rate(active_days_count: int, cfg: GlobalConfig) -> float:
    """Per-active-day interest rate, decaying from the base toward the floor the
    longer the deposit has been earning. ``active_days_count`` is the number of
    days already credited (0 on the first accrual)."""
    base = cfg.dep_rate_pct / 100
    floor = cfg.dep_rate_floor_pct / 100
    decay = cfg.dep_rate_decay_pct / 100
    rate = base * ((1 - decay) ** max(0, active_days_count))
    return max(floor, rate)


def deposit_day_interest(
    principal: int, accrued: int, active_days_count: int, cfg: GlobalConfig
) -> int:
    """Interest to credit for one active day, respecting the total yield cap."""
    if principal <= 0:
        return 0
    rate = effective_deposit_rate(active_days_count, cfg)
    raw = int(principal * rate)
    # A small principal can truncate to 0 even at a positive rate. As long as the
    # yield cap leaves headroom, pay a floor of 1 so small deposits aren't pointless.
    if raw == 0 and rate > 0:
        raw = 1
    cap_total = principal * cfg.dep_yield_cap_pct // 100
    headroom = max(0, cap_total - accrued)
    return max(0, min(raw, headroom))


def credit_multiplier(loans_repaid: int, loans_defaulted: int) -> float:
    """Trust factor: rises with clean repayments, falls with defaults."""
    raw = 1.0 + 0.25 * loans_repaid - 0.5 * loans_defaulted
    return max(0.0, min(3.0, raw))


def max_loan(size: int, loans_repaid: int, loans_defaulted: int, cfg: GlobalConfig) -> int:
    mult = credit_multiplier(loans_repaid, loans_defaulted)
    base = int(size * cfg.loan_max_base_pct / 100 * mult)
    # Even a broke (size 0) player gets a starter line, so newcomers can borrow —
    # but a player who has burned the house (mult 0 via defaults) stays shut out.
    if mult > 0:
        return max(base, cfg.loan_min)
    return max(0, base)


def loan_interest_accrued(principal: int, full_days: int, cfg: GlobalConfig) -> int:
    if principal <= 0 or full_days <= 0:
        return 0
    return int(principal * (cfg.loan_rate_pct / 100) * full_days)


# --------------------------------------------------------------------------- #
# Read model for the panel / profile.
# --------------------------------------------------------------------------- #


@dataclass
class DepositView:
    principal: int
    accrued: int
    matures_at: int
    matured: bool
    active_days: int


@dataclass
class LoanView:
    principal: int
    interest: int
    debt: int
    due_at: int
    defaulted: bool


@dataclass
class BankSummary:
    size: int
    deposit: DepositView | None
    loan: LoanView | None
    loans_repaid: int
    loans_defaulted: int
    loan_limit: int


async def get_summary(chat_id: int, user_id: int) -> BankSummary:
    cfg = get_config_sync()
    player = await players_repo.get_player(chat_id, user_id)
    size = player.size if player else 0
    repaid = player.loans_repaid if player else 0
    defaulted_n = player.loans_defaulted if player else 0

    dep_row = await repo.get_deposit(chat_id, user_id)
    dep_view = None
    if dep_row is not None and dep_row.principal > 0:
        dep_view = DepositView(
            principal=dep_row.principal,
            accrued=dep_row.accrued,
            matures_at=dep_row.matures_at,
            matured=_now() >= dep_row.matures_at,
            active_days=dep_row.active_days_count,
        )

    loan_row = await repo.get_loan(chat_id, user_id)
    loan_view = None
    if loan_row is not None and (loan_row.principal > 0 or loan_row.accrued_interest > 0):
        loan_view = LoanView(
            principal=loan_row.principal,
            interest=loan_row.accrued_interest,
            debt=loan_row.principal + loan_row.accrued_interest,
            due_at=loan_row.due_at,
            defaulted=bool(loan_row.defaulted),
        )

    # The Corporation lends its own cash, so what you can actually borrow is the
    # smaller of your credit limit and the money currently in the till.
    corp = await repo.get_corp()
    credit_limit = max_loan(size, repaid, defaulted_n, cfg)
    available = min(credit_limit, max(0, corp.balance))

    return BankSummary(
        size=size,
        deposit=dep_view,
        loan=loan_view,
        loans_repaid=repaid,
        loans_defaulted=defaulted_n,
        loan_limit=available,
    )


# --------------------------------------------------------------------------- #
# Errors / results.
# --------------------------------------------------------------------------- #


class BankError(Exception):
    """User-facing failure; ``code`` selects the crude-humour text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class OpResult:
    amount: int
    extra: int = 0  # penalty / interest / forfeit, depending on the op


# --------------------------------------------------------------------------- #
# Deposit ops (called outside the game handlers; take the chat lock here).
# --------------------------------------------------------------------------- #


async def open_deposit(chat_id: int, user_id: int, amount: int) -> OpResult:
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id):
        player = await players_repo.get_player(chat_id, user_id)
        if player is None or player.size <= 0:
            raise BankError("no_size")
        amount = max(1, min(amount, player.size))

        dep = await repo.get_deposit(chat_id, user_id)
        now = _now()
        matures_at = now + cfg.dep_term_days * DAY
        if dep is not None and dep.principal > 0:
            new_principal = dep.principal + amount
            # Top-up keeps the later maturity so a fresh chunk cannot be pulled early.
            matures_at = max(dep.matures_at, matures_at)
            await repo.upsert_deposit(
                chat_id, user_id, principal=new_principal, matures_at=matures_at
            )
        else:
            await repo.upsert_deposit(
                chat_id,
                user_id,
                principal=amount,
                accrued=0,
                opened_at=now,
                matures_at=matures_at,
                active_days_count=0,
                last_accrual_day="",
            )

        await players_repo.set_player_fields(chat_id, user_id, size=player.size - amount)
        # The principal joins the Corporation's till — that is the cash it lends out.
        async with repo.corp_lock():
            await repo.corp_apply(delta=amount)
        await E.log_event(
            chat_id, user_id, E.DEPOSIT_OPEN, delta=-amount, size_after=player.size - amount
        )
    return OpResult(amount=amount)


async def withdraw_deposit(chat_id: int, user_id: int, amount: int | None) -> OpResult:
    """Withdraw ``amount`` of principal (None = all). Returns the credited amount
    and the penalty+forfeited interest withheld by the Corporation."""
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id):
        dep = await repo.get_deposit(chat_id, user_id)
        if dep is None or dep.principal <= 0:
            raise BankError("no_deposit")

        w = dep.principal if amount is None else max(1, min(amount, dep.principal))
        accrued_share = dep.accrued * w // dep.principal if dep.principal else 0
        matured = _now() >= dep.matures_at

        if matured:
            credited = w + accrued_share
            penalty = 0
            # Principal leaves the till back to the depositor (the accrued part was
            # already paid out of the till when it was earned). A drained till can
            # go negative here — that is a bank run, i.e. the bankruptcy event.
            async with repo.corp_lock():
                await repo.corp_apply(delta=-w)
        else:
            penalty = (w * cfg.dep_early_penalty_pct + 99) // 100  # ceil
            credited = max(0, w - penalty)
            # Principal (minus the retained penalty) leaves the till; the forfeited,
            # pre-paid interest is reclaimed by the house. Both penalty and forfeited
            # interest count as house earnings.
            async with repo.corp_lock():
                await repo.corp_apply(
                    delta=accrued_share - (w - penalty),
                    penalties=penalty + accrued_share,
                )
            if penalty or accrued_share:
                await E.log_event(
                    chat_id, user_id, E.DEPOSIT_PENALTY,
                    meta={"penalty": penalty, "forfeit_interest": accrued_share},
                )

        rem_principal = dep.principal - w
        rem_accrued = dep.accrued - accrued_share
        player = await players_repo.get_player(chat_id, user_id)
        new_size = (player.size if player else 0) + credited
        await players_repo.set_player_fields(chat_id, user_id, size=new_size)

        if rem_principal <= 0:
            await repo.delete_deposit(chat_id, user_id)
        else:
            await repo.upsert_deposit(
                chat_id, user_id, principal=rem_principal, accrued=max(0, rem_accrued)
            )

        await E.log_event(
            chat_id, user_id, E.DEPOSIT_WITHDRAW, delta=credited, size_after=new_size
        )
    return OpResult(amount=credited, extra=penalty + (0 if matured else accrued_share))


async def accrue_deposit_on_play(chat_id: int, user_id: int, today: str) -> int:
    """Credit one active day's interest if the owner has a deposit and has not
    already earned today. Paid out of the Corporation. Returns interest credited."""
    cfg = get_config_sync()
    dep = await repo.get_deposit(chat_id, user_id)
    if dep is None or dep.principal <= 0 or dep.last_accrual_day == today:
        return 0
    interest = deposit_day_interest(dep.principal, dep.accrued, dep.active_days_count, cfg)
    # The Corporation pays this out of its own till. A broke house pays nothing —
    # and we don't burn the active day, so the depositor can still earn once the
    # till recovers. Interest is capped to whatever cash the Corporation has. The
    # corp lock keeps the cap check and the payout consistent across chats.
    async with repo.corp_lock():
        corp = await repo.get_corp()
        interest = min(interest, max(0, corp.balance))
        if interest <= 0:
            return 0
        await repo.upsert_deposit(
            chat_id,
            user_id,
            accrued=dep.accrued + interest,
            active_days_count=dep.active_days_count + 1,
            last_accrual_day=today,
        )
        await repo.corp_apply(delta=-interest, interest_paid=interest)
    await E.log_event(chat_id, user_id, E.DEPOSIT_INTEREST, meta={"interest": interest})
    return interest


# --------------------------------------------------------------------------- #
# Loan ops.
# --------------------------------------------------------------------------- #


async def take_loan(chat_id: int, user_id: int, amount: int) -> OpResult:
    cfg = get_config_sync()
    # A credit-history rejection puts the applicant in the penalty box: they can't
    # re-apply until the cooldown lapses (no spamming the till after a refusal).
    if not cooldown.peek(chat_id, user_id, _LOAN_DENY_KEY, cfg.loan_deny_cooldown_sec):
        raise BankError("loan_denied")
    async with players_repo.get_chat_lock(chat_id):
        existing = await repo.get_loan(chat_id, user_id)
        if existing is not None and (existing.principal > 0 or existing.accrued_interest > 0):
            raise BankError("loan_exists")
        player = await players_repo.get_player(chat_id, user_id)
        size = player.size if player else 0
        repaid = player.loans_repaid if player else 0
        defaulted_n = player.loans_defaulted if player else 0
        credit_limit = max_loan(size, repaid, defaulted_n, cfg)
        if credit_limit < 1:
            cooldown.touch(chat_id, user_id, _LOAN_DENY_KEY)
            raise BankError("no_credit")
        # The money comes out of the Corporation's till — it can't lend what it
        # doesn't have, and it never lends itself into the red. Hold the corp lock
        # across the read+debit so two chats can't both drain the same cash.
        async with repo.corp_lock():
            corp = await repo.get_corp()
            available = max(0, corp.balance)
            if available < 1:
                raise BankError("corp_broke")
            amount = max(1, min(amount, credit_limit, available))

            now = _now()
            await repo.upsert_loan(
                chat_id, user_id,
                principal=amount, accrued_interest=0, opened_at=now,
                due_at=now + cfg.loan_term_days * DAY, last_accrual_at=now,
                last_reminded_at=0, defaulted=False,
            )
            new_size = size + amount
            await players_repo.set_player_fields(chat_id, user_id, size=new_size)
            await repo.corp_apply(delta=-amount)  # cash leaves the vault into the borrower
        await E.log_event(
            chat_id, user_id, E.LOAN_OPEN, delta=amount, size_after=new_size,
            meta={"due_at": now + cfg.loan_term_days * DAY},
        )
    return OpResult(amount=amount)


async def repay_loan(chat_id: int, user_id: int, amount: int | None) -> OpResult:
    """Repay ``amount`` from liquid size (None = as much as possible). Interest
    portion becomes Corporation income; principal portion is burned."""
    async with players_repo.get_chat_lock(chat_id):
        loan = await repo.get_loan(chat_id, user_id)
        if loan is None or (loan.principal <= 0 and loan.accrued_interest <= 0):
            raise BankError("no_loan")
        player = await players_repo.get_player(chat_id, user_id)
        size = player.size if player else 0
        if size <= 0:
            raise BankError("no_size")
        debt = loan.principal + loan.accrued_interest
        pay = debt if amount is None else amount
        pay = max(1, min(pay, size, debt))

        interest_part = min(pay, loan.accrued_interest)
        principal_part = pay - interest_part
        new_size = size - pay
        await players_repo.set_player_fields(chat_id, user_id, size=new_size)
        # The full payment returns to the Corporation: principal refills the till,
        # interest is its profit.
        await repo.corp_apply(delta=pay, interest_earned=interest_part)

        remaining = debt - pay
        if remaining <= 0:
            await repo.delete_loan(chat_id, user_id)
            repaid = (player.loans_repaid if player else 0) + 1
            await players_repo.set_player_fields(chat_id, user_id, loans_repaid=repaid)
        else:
            await repo.upsert_loan(
                chat_id, user_id,
                accrued_interest=loan.accrued_interest - interest_part,
                principal=loan.principal - principal_part,
            )
        await E.log_event(
            chat_id, user_id, E.LOAN_REPAY, delta=-pay, size_after=new_size,
            meta={"interest": interest_part, "cleared": remaining <= 0},
        )
    return OpResult(amount=pay, extra=interest_part)


# --------------------------------------------------------------------------- #
# Garnishment — invoked from inside /dick and /duel, which already hold the lock
# and own the in-memory player dict. We mutate the dict's size and persist the
# loan; the caller's save_storage writes the player back.
# --------------------------------------------------------------------------- #


async def garnish_on_dict(chat_id: int, user_id: int, player_dict: dict, gain: int) -> int:
    """If the player has a defaulted loan and a positive ``gain``, divert a slice
    toward the debt. Mutates ``player_dict['size']`` and returns the diverted sum."""
    if gain <= 0:
        return 0
    cfg = get_config_sync()
    return await _garnish(chat_id, user_id, player_dict, gain, cfg.loan_garnish_pct)


async def garnish_duel_on_dict(chat_id: int, user_id: int, player_dict: dict, profit: int) -> int:
    if profit <= 0:
        return 0
    cfg = get_config_sync()
    return await _garnish(chat_id, user_id, player_dict, profit, cfg.loan_duel_garnish_pct)


async def _garnish(chat_id: int, user_id: int, player_dict: dict, base: int, pct: int) -> int:
    loan = await repo.get_loan(chat_id, user_id)
    if loan is None or not loan.defaulted:
        return 0
    debt = loan.principal + loan.accrued_interest
    if debt <= 0:
        return 0
    take = min(debt, player_dict.get("size", 0), (base * pct + 99) // 100)
    if take <= 0:
        return 0
    interest_part = min(take, loan.accrued_interest)
    principal_part = take - interest_part
    player_dict["size"] = max(0, player_dict.get("size", 0) - take)
    # Recovered money flows back to the Corporation (principal + interest profit).
    async with repo.corp_lock():
        await repo.corp_apply(delta=take, interest_earned=interest_part)
    remaining = debt - take
    if remaining <= 0:
        # Forced recovery on a defaulted loan clears the debt but does NOT count as
        # a clean repayment — only voluntary repay_loan improves credit history.
        await repo.delete_loan(chat_id, user_id)
    else:
        await repo.upsert_loan(
            chat_id, user_id,
            accrued_interest=loan.accrued_interest - interest_part,
            principal=loan.principal - principal_part,
        )
    await E.log_event(
        chat_id, user_id, E.LOAN_GARNISH, delta=-take,
        size_after=player_dict["size"], meta={"cleared": remaining <= 0},
    )
    return take


# --------------------------------------------------------------------------- #
# Collector helpers (background loop in bot.py).
# --------------------------------------------------------------------------- #


async def accrue_loan_interest(loan, cfg: GlobalConfig, now: int) -> int:
    """Grow the debt by whole calendar days elapsed since the last accrual.
    Advances ``last_accrual_at`` only by consumed full days (small principals do
    not silently lose interest to truncation)."""
    # A defaulted debt is frozen: once the loan defaults the balance stops growing,
    # so it can't spiral beyond what garnishment/deposit recovery can ever clear.
    if loan.defaulted:
        return 0
    full_days = (now - loan.last_accrual_at) // DAY
    if full_days <= 0:
        return 0
    interest = loan_interest_accrued(loan.principal, full_days, cfg)
    await repo.upsert_loan(
        loan.chat_id, loan.user_id,
        accrued_interest=loan.accrued_interest + interest,
        last_accrual_at=loan.last_accrual_at + full_days * DAY,
    )
    if interest:
        await E.log_event(
            loan.chat_id, loan.user_id, E.LOAN_INTEREST, meta={"interest": interest}
        )
    return interest


async def mark_default(loan) -> None:
    await repo.upsert_loan(loan.chat_id, loan.user_id, defaulted=True)
    player = await players_repo.get_player(loan.chat_id, loan.user_id)
    defaulted_n = (player.loans_defaulted if player else 0) + 1
    await players_repo.set_player_fields(loan.chat_id, loan.user_id, loans_defaulted=defaulted_n)
    await E.log_event(loan.chat_id, loan.user_id, E.LOAN_DEFAULT)


async def recover_from_deposit(loan) -> int:
    """A deposit is not a shelter from a defaulted debt: pull the owed amount out of
    the debtor's own deposit principal. Returns the amount recovered (0 if none).

    The deposit principal already sits in the Corporation's till (it funded it on
    open), so no cash moves — we only shrink the depositor's claim and the debt, and
    book the interest slice as house earnings (the same no-cash-move pattern as
    confiscation). Forced recovery does NOT improve credit history."""
    if not loan.defaulted:
        return 0
    debt = loan.principal + loan.accrued_interest
    if debt <= 0:
        return 0
    dep = await repo.get_deposit(loan.chat_id, loan.user_id)
    if dep is None or dep.principal <= 0:
        return 0
    take = min(debt, dep.principal)
    if take <= 0:
        return 0
    interest_part = min(take, loan.accrued_interest)
    principal_part = take - interest_part

    rem_dep = dep.principal - take
    if rem_dep <= 0:
        await repo.delete_deposit(loan.chat_id, loan.user_id)
    else:
        await repo.upsert_deposit(loan.chat_id, loan.user_id, principal=rem_dep)
    # Cash already in the till; only book the interest slice as earnings.
    async with repo.corp_lock():
        await repo.corp_apply(delta=0, interest_earned=interest_part)

    remaining = debt - take
    if remaining <= 0:
        await repo.delete_loan(loan.chat_id, loan.user_id)
    else:
        await repo.upsert_loan(
            loan.chat_id, loan.user_id,
            accrued_interest=loan.accrued_interest - interest_part,
            principal=loan.principal - principal_part,
        )
    await E.log_event(
        loan.chat_id, loan.user_id, E.LOAN_GARNISH, delta=-take,
        meta={"cleared": remaining <= 0, "from_deposit": True},
    )
    return take


async def roll_confiscation(
    dep, cfg: GlobalConfig, today: str = "", rng: random.Random | None = None
) -> int:
    """With probability ``dep_confisc_chance_pct`` seize up to ``dep_confisc_max_pct``
    of the principal for the Corporation. Returns the seized amount (0 if none).

    ``today`` (UTC ISO date) gates the roll to at most one attempt per calendar day
    so the chance is per-day, not per-collector-run (the loop can fire hourly)."""
    if dep.principal <= 0 or cfg.dep_confisc_chance_pct <= 0:
        return 0
    if today and dep.last_confisc_day == today:
        return 0
    r = rng or random
    fired = r.random() < cfg.dep_confisc_chance_pct / 100
    # Mark the day as rolled whether or not it fired, so a missed roll isn't
    # retried on the next run within the same day.
    if today and not fired:
        await repo.upsert_deposit(dep.chat_id, dep.user_id, last_confisc_day=today)
        return 0
    if not fired:
        return 0
    frac = r.uniform(0, cfg.dep_confisc_max_pct / 100)
    seized = int(dep.principal * frac)
    if seized <= 0:
        if today:
            await repo.upsert_deposit(dep.chat_id, dep.user_id, last_confisc_day=today)
        return 0
    await repo.upsert_deposit(
        dep.chat_id, dep.user_id, principal=dep.principal - seized, last_confisc_day=today
    )
    # The seized cash is already sitting in the till (deposits fund it). We only
    # shrink the depositor's claim and book it as house earnings — no cash moves.
    await repo.corp_apply(delta=0, penalties=seized)
    await E.log_event(
        dep.chat_id, dep.user_id, E.CONFISCATION, delta=-seized, meta={"seized": seized}
    )
    return seized


# --------------------------------------------------------------------------- #
# Corporation read.
# --------------------------------------------------------------------------- #


async def corp_state():
    return await repo.get_corp()


async def credit_corp_tax(chat_id: int, user_id: int, amount: int) -> None:
    """Funnel the duel house-cut into the Corporation (previously it vanished)."""
    if amount <= 0:
        return
    await repo.corp_apply(delta=amount, tax=amount)
    await E.log_event(chat_id, user_id, E.CORP_TAX, meta={"tax": amount})


# --------------------------------------------------------------------------- #
# Background collector (driven by the loop in bot.py).
# --------------------------------------------------------------------------- #


async def _maybe_remind(bot, loan, cfg: GlobalConfig, now: int) -> None:
    if now - loan.last_reminded_at < cfg.reminder_cooldown_sec:
        return
    debt = loan.principal + loan.accrued_interest
    if debt <= 0:
        return
    import texts

    overdue_for = max(0, now - loan.due_at)
    try:
        await bot.send_message(loan.user_id, texts.collector_reminder(debt, overdue_for))
    except Exception:
        # The debtor may never have opened a DM with the bot; skip silently and
        # try again next cycle (the cooldown flag is only armed on a real send).
        return
    await repo.upsert_loan(loan.chat_id, loan.user_id, last_reminded_at=now)


async def run_collector_pass(bot) -> None:
    """One sweep: grow loan interest, default the overdue, nag debtors in DM, and
    roll deposit confiscations. Each step is best-effort and independent."""
    cfg = get_config_sync()
    now = _now()
    today = datetime.now(timezone.utc).date().isoformat()

    for loan in await repo.all_loans():
        if loan.principal <= 0 and loan.accrued_interest <= 0:
            continue
        await accrue_loan_interest(loan, cfg, now)
        fresh = await repo.get_loan(loan.chat_id, loan.user_id)
        if fresh is None:
            continue
        if not fresh.defaulted and now >= fresh.due_at:
            await mark_default(fresh)
            fresh = await repo.get_loan(loan.chat_id, loan.user_id)
        if fresh is not None and fresh.defaulted:
            # Pull from the debtor's deposit first, then nag about whatever remains.
            await recover_from_deposit(fresh)
            fresh = await repo.get_loan(loan.chat_id, loan.user_id)
            if fresh is not None:
                await _maybe_remind(bot, fresh, cfg, now)

    for dep in await repo.all_deposits():
        if dep.principal > 0:
            await roll_confiscation(dep, cfg, today)
