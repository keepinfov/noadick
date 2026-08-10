"""Economy core: deposits, loans and chat-local Corporation accounts.

Money rules (kept deliberately simple but internally consistent):

* Deposit principal is **moved** out of the player's liquid ``size`` (it freezes:
  hidden from /top, unusable in duels, no /dick growth). Interest is paid **by the
  Corporation** (its balance shrinks) and accrues only on days the owner actually
  plays /dick. The effective rate decays per active day and total yield is capped,
  so "deposit and forget" never pays off. Early withdrawal forfeits accrued
  interest and pays a penalty to the Corporation; deposits can also be randomly
  (partially) confiscated by the Corporation.
* Loan principal leaves the Corporation till and becomes liquid ``size``. A
  repayment refills the till; its interest slice is house profit. Interest grows
  the debt by calendar time and, when repaid or garnished, becomes Corporation
  income. Past the due date the loan defaults and is recovered by garnishing
  /dick gains and duel winnings.
* Every group has an isolated Corporation. A 25% liquidity reserve limits risk;
  failed withdrawals trigger a seven-day sanction and uninsured deposit bail-in.

The pure helpers (rates, limits, penalties) take a config snapshot and are unit
tested; the async ops below wrap them with repository IO. Callers that already hold
the per-chat lock (the /dick and /duel handlers) use the ``*_on_dict`` helpers so we
never fight their in-memory player dict.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError

from repositories import bank as repo
from repositories import events as E
from repositories import players as players_repo
from repositories import poker as poker_repo
from services import cooldown
from services.global_settings import GlobalConfig, get_config_sync

_LOAN_DENY_KEY = "loan_denied"

DAY = 86400
PPM = 1_000_000


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
    """Whole-unit interest for one active day, without fractional carry."""
    interest, _ = deposit_day_credit(principal, accrued, active_days_count, 0, cfg)
    return interest


def deposit_day_credit(
    principal: int,
    accrued: int,
    active_days_count: int,
    remainder_ppm: int,
    cfg: GlobalConfig,
) -> tuple[int, int]:
    """Return ``(whole interest, fractional remainder)`` for one active day.

    The remainder is stored in millionths of one size unit. Carrying it forward
    makes small deposits proportional while avoiding the old guaranteed +1.
    """
    if principal <= 0:
        return 0, 0
    rate = effective_deposit_rate(active_days_count, cfg)
    cap_total = principal * cfg.dep_yield_cap_pct // 100
    headroom = max(0, cap_total - accrued)
    if headroom <= 0:
        return 0, 0
    rate_ppm = max(0, int(rate * PPM))
    units = principal * rate_ppm + max(0, remainder_ppm)
    whole, remainder = divmod(units, PPM)
    interest = max(0, min(whole, headroom))
    if interest >= headroom:
        remainder = 0
    return interest, remainder


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


def pisyago_coverage_pct(assets: int, threshold: int) -> int:
    """Return the progressive PISYAGO coverage tier for gross assets.

    Four equal quarters of the configured threshold map to 100/75/50/25%.
    Gross assets are deliberate: debts never make a rich borrower look poor.
    """
    assets = max(0, assets)
    if threshold <= 0 or assets >= threshold:
        return 0
    if assets * 4 < threshold:
        return 100
    if assets * 4 < threshold * 2:
        return 75
    if assets * 4 < threshold * 3:
        return 50
    return 25


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
    insured: int = 0
    insurance_expires_at: int = 0


@dataclass
class LoanView:
    principal: int
    interest: int
    debt: int
    due_at: int
    defaulted: bool
    roll_debt: int


@dataclass
class PisyagoView:
    assets: int
    threshold: int
    coverage_pct: int
    remaining: int
    limit: int
    reset_at: int


@dataclass(frozen=True)
class PisyagoResult:
    loss: int
    assets: int
    coverage_pct: int
    covered: int
    debt: int
    remaining: int
    reset_at: int


@dataclass
class BankSummary:
    size: int
    deposit: DepositView | None
    loan: LoanView | None
    loans_repaid: int
    loans_defaulted: int
    loan_limit: int
    pisyago: PisyagoView
    next_credit_reward_at: int = 0


@dataclass(frozen=True)
class DickPayout:
    nominal: int
    credited: int
    emitted: int
    corporation_paid: int
    clipped: int


@dataclass(frozen=True)
class CorpView:
    chat_id: int
    balance: int
    insurance_reserve: int
    status: str
    sanation_deadline: int
    bankruptcy_count: int
    deposits: int
    reserve_required: int
    spendable: int
    total_tax: int
    total_interest_earned: int
    total_interest_paid: int
    total_penalties: int
    total_poker_rake: int
    total_emission: int
    total_bailin: int


async def insured_principal(
    chat_id: int, user_id: int, principal: int, now: int | None = None
) -> tuple[int, int]:
    now = _now() if now is None else now
    policies = await repo.active_insurance(chat_id, user_id, now)
    amount = min(max(0, principal), sum(max(0, p.amount) for p in policies))
    expiry = max((p.expires_at for p in policies), default=0)
    return amount, expiry


def required_reserve(liability: int, cfg: GlobalConfig) -> int:
    return max(0, liability) * cfg.corp_liquidity_reserve_pct // 100


async def _start_sanation(chat_id: int) -> None:
    corp = await repo.get_corp(chat_id)
    if corp.status != "healthy":
        return
    now = _now()
    cfg = get_config_sync()
    await repo.set_corp_fields(
        chat_id,
        status="sanation",
        sanation_started_at=now,
        sanation_deadline=now + cfg.corp_sanation_days * DAY,
    )


async def get_summary(chat_id: int, user_id: int) -> BankSummary:
    cfg = get_config_sync()
    player = await players_repo.get_player(chat_id, user_id)
    size = player.size if player else 0
    repaid = player.loans_repaid if player else 0
    defaulted_n = player.loans_defaulted if player else 0

    dep_row = await repo.get_deposit(chat_id, user_id)
    dep_view = None
    if dep_row is not None and dep_row.principal > 0:
        insured, insurance_expires_at = await insured_principal(chat_id, user_id, dep_row.principal)
        dep_view = DepositView(
            principal=dep_row.principal,
            accrued=dep_row.accrued,
            matures_at=dep_row.matures_at,
            matured=_now() >= dep_row.matures_at,
            active_days=dep_row.active_days_count,
            insured=insured,
            insurance_expires_at=insurance_expires_at,
        )

    poker_stack = await poker_repo.get_money_stack(chat_id, user_id)
    deposit_assets = (
        dep_row.principal + dep_row.accrued if dep_row is not None and dep_row.principal > 0 else 0
    )
    assets = max(0, size) + deposit_assets + poker_stack
    now = _now()
    window_active = bool(player and player.insurance_reset_at > now)
    used = max(0, int(player.insurance_used)) if window_active and player else 0
    insurance_limit = max(0, cfg.dick_insurance_limit)
    pisyago = PisyagoView(
        assets=assets,
        threshold=cfg.dick_insurance_threshold,
        coverage_pct=(
            pisyago_coverage_pct(assets, cfg.dick_insurance_threshold) if insurance_limit > 0 else 0
        ),
        remaining=max(0, insurance_limit - used),
        limit=insurance_limit,
        reset_at=int(player.insurance_reset_at) if window_active and player else 0,
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
            roll_debt=int(loan_row.roll_debt_principal),
        )

    # The Corporation lends its own cash, so what you can actually borrow is the
    # smaller of your credit limit and the money currently in the till.
    corp = await repo.get_corp(chat_id)
    credit_limit = max_loan(size, repaid, defaulted_n, cfg)
    liability = await repo.deposit_liability(chat_id)
    spendable = max(0, corp.balance - required_reserve(liability, cfg))
    available = min(credit_limit, spendable) if corp.status == "healthy" else 0

    return BankSummary(
        size=size,
        deposit=dep_view,
        loan=loan_view,
        loans_repaid=repaid,
        loans_defaulted=defaulted_n,
        loan_limit=available,
        pisyago=pisyago,
        next_credit_reward_at=(player.last_credit_reward_at if player else 0)
        + cfg.credit_reward_cooldown_days * DAY,
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


async def apply_pisyago_on_dict(chat_id: int, user_id: int, loss: int) -> PisyagoResult:
    """Cover part of a negative /dick roll for a low-asset player.

    The caller owns the per-chat lock. The allowance window starts on the first
    actually covered centimetre and renews after the configured period. Only
    liquid size, deposits and real-money poker escrow count as assets; debt is
    intentionally ignored rather than subtracted.
    """
    if loss <= 0:
        raise ValueError("loss must be positive")

    cfg = get_config_sync()
    player = await players_repo.get_player(chat_id, user_id)
    deposit = await repo.get_deposit(chat_id, user_id)
    poker_stack = await poker_repo.get_money_stack(chat_id, user_id)
    liquid = max(0, int(player.size)) if player else 0
    deposit_assets = max(0, int(deposit.principal)) + max(0, int(deposit.accrued)) if deposit else 0
    assets = liquid + deposit_assets + poker_stack

    limit = max(0, cfg.dick_insurance_limit)
    coverage_pct = pisyago_coverage_pct(assets, cfg.dick_insurance_threshold) if limit > 0 else 0
    now = _now()
    reset_at = int(player.insurance_reset_at) if player else 0
    used = max(0, int(player.insurance_used)) if player else 0
    if reset_at <= now:
        used = 0
        reset_at = 0

    nominal_cover = loss * coverage_pct // 100
    covered = min(nominal_cover, max(0, limit - used))
    if covered > 0:
        if reset_at == 0:
            reset_at = now + cfg.dick_insurance_period_days * DAY
        used += covered
        await players_repo.set_player_fields(
            chat_id,
            user_id,
            insurance_used=used,
            insurance_reset_at=reset_at,
        )

    debt = loss - covered
    remaining = max(0, limit - used)
    result = PisyagoResult(
        loss=loss,
        assets=assets,
        coverage_pct=coverage_pct,
        covered=covered,
        debt=debt,
        remaining=remaining,
        reset_at=reset_at,
    )
    if covered > 0:
        await E.log_event(
            chat_id,
            user_id,
            E.PISYAGO,
            meta={
                "loss": loss,
                "assets": assets,
                "coverage_pct": coverage_pct,
                "covered": covered,
                "debt": debt,
                "remaining": remaining,
                "reset_at": reset_at,
            },
        )
    return result


# --------------------------------------------------------------------------- #
# Deposit ops (called outside the game handlers; take the chat lock here).
# --------------------------------------------------------------------------- #


async def buy_sekasko(chat_id: int, user_id: int, amount: int) -> OpResult:
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id):
        corp = await repo.get_corp(chat_id)
        if corp.status != "healthy":
            raise BankError("corp_frozen")
        dep = await repo.get_deposit(chat_id, user_id)
        if dep is None or dep.principal <= 0:
            raise BankError("no_deposit")
        covered, _ = await insured_principal(chat_id, user_id, dep.principal)
        available = min(dep.principal, cfg.sekasko_max_coverage) - covered
        if amount < 1 or amount > available:
            raise BankError("insurance_limit")
        premium = max(1, (amount * cfg.sekasko_premium_pct + 99) // 100)
        player = await players_repo.get_player(chat_id, user_id)
        if player is None or player.size < premium:
            raise BankError("insurance_cash")
        await players_repo.set_player_fields(chat_id, user_id, size=player.size - premium)
        expires_at = _now() + cfg.dep_term_days * DAY
        await repo.add_insurance(chat_id, user_id, amount, premium, expires_at)
        await repo.corp_apply(
            chat_id,
            delta=0,
            insurance_delta=premium,
            reason="sekasko_premium",
            user_id=user_id,
            meta={"insured": amount, "expires_at": expires_at},
        )
        await E.log_event(
            chat_id,
            user_id,
            E.DEPOSIT_INSURANCE,
            delta=-premium,
            size_after=player.size - premium,
            meta={"insured": amount, "premium": premium, "expires_at": expires_at},
        )
        return OpResult(amount=amount, extra=premium)


async def open_deposit(chat_id: int, user_id: int, amount: int) -> OpResult:
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id):
        if (await repo.get_corp(chat_id)).status != "healthy":
            raise BankError("corp_frozen")
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
                interest_remainder_ppm=0,
            )

        await players_repo.set_player_fields(chat_id, user_id, size=player.size - amount)
        # The principal joins the Corporation's till — that is the cash it lends out.
        async with repo.corp_lock(chat_id):
            await repo.corp_apply(chat_id, delta=amount, reason="deposit_open", user_id=user_id)
        await E.log_event(
            chat_id, user_id, E.DEPOSIT_OPEN, delta=-amount, size_after=player.size - amount
        )
    return OpResult(amount=amount)


async def withdraw_deposit(chat_id: int, user_id: int, amount: int | None) -> OpResult:
    """Withdraw ``amount`` of principal (None = all). Returns the credited amount
    and the penalty+forfeited interest withheld by the Corporation."""
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id):
        corp = await repo.get_corp(chat_id)
        if corp.status != "healthy":
            raise BankError("corp_frozen")
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
            cash_delta = -credited
        else:
            penalty = (w * cfg.dep_early_penalty_pct + 99) // 100  # ceil
            credited = max(0, w - penalty)
            # Principal (minus the retained penalty) leaves the till; the forfeited,
            # pre-paid interest is reclaimed by the house. Both penalty and forfeited
            # interest count as house earnings.
            cash_delta = -credited

        liability = await repo.deposit_liability(chat_id)
        remaining_liability = max(0, liability - w - accrued_share)
        projected_cash = corp.balance + cash_delta
        if projected_cash < required_reserve(remaining_liability, cfg):
            await _start_sanation(chat_id)
            raise BankError("corp_sanation")
        async with repo.corp_lock(chat_id):
            await repo.corp_apply(
                chat_id,
                delta=cash_delta,
                penalties=0 if matured else penalty + accrued_share,
                reason="deposit_withdraw",
                user_id=user_id,
                meta={"matured": matured, "penalty": penalty, "interest": accrued_share},
            )
        if not matured and (penalty or accrued_share):
            await E.log_event(
                chat_id,
                user_id,
                E.DEPOSIT_PENALTY,
                meta={"penalty": penalty, "forfeit_interest": accrued_share},
            )

        rem_principal = dep.principal - w
        rem_accrued = dep.accrued - accrued_share
        rem_remainder = (
            dep.interest_remainder_ppm * rem_principal // dep.principal if dep.principal else 0
        )
        player = await players_repo.get_player(chat_id, user_id)
        new_size = (player.size if player else 0) + credited
        await players_repo.set_player_fields(chat_id, user_id, size=new_size)

        if rem_principal <= 0:
            await repo.delete_deposit(chat_id, user_id)
            await repo.delete_insurance_for_deposit(chat_id, user_id)
        else:
            await repo.upsert_deposit(
                chat_id,
                user_id,
                principal=rem_principal,
                accrued=max(0, rem_accrued),
                interest_remainder_ppm=max(0, rem_remainder),
            )

        await E.log_event(chat_id, user_id, E.DEPOSIT_WITHDRAW, delta=credited, size_after=new_size)
    return OpResult(amount=credited, extra=penalty + (0 if matured else accrued_share))


async def accrue_deposit_on_play(chat_id: int, user_id: int, today: str) -> int:
    """Credit one active day's interest if the owner has a deposit and has not
    already earned today. Paid out of the Corporation. Returns interest credited."""
    cfg = get_config_sync()
    dep = await repo.get_deposit(chat_id, user_id)
    if dep is None or dep.principal <= 0 or dep.last_accrual_day == today:
        return 0
    interest, remainder = deposit_day_credit(
        dep.principal,
        dep.accrued,
        dep.active_days_count,
        dep.interest_remainder_ppm,
        cfg,
    )
    cap_total = dep.principal * cfg.dep_yield_cap_pct // 100
    if dep.accrued >= cap_total:
        return 0
    # Interest is a new liability, not a cash payout. It can only consume free
    # corporate capital; the actual cash leaves when the deposit is withdrawn.
    async with repo.corp_lock(chat_id):
        corp = await repo.get_corp(chat_id)
        if corp.status != "healthy":
            return 0
        liability = await repo.deposit_liability(chat_id)
        available = max(0, corp.balance - liability)
        if interest > 0 and available <= 0:
            return 0
        owed = interest
        interest = min(interest, available)
        if interest < owed:
            remainder += (owed - interest) * PPM
        await repo.upsert_deposit(
            chat_id,
            user_id,
            accrued=dep.accrued + interest,
            active_days_count=dep.active_days_count + 1,
            last_accrual_day=today,
            interest_remainder_ppm=remainder,
        )
        if interest:
            await repo.corp_apply(
                chat_id,
                delta=0,
                interest_paid=interest,
                reason="deposit_interest",
                user_id=user_id,
            )
    if interest:
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
        async with repo.corp_lock(chat_id):
            corp = await repo.get_corp(chat_id)
            if corp.status != "healthy":
                raise BankError("corp_frozen")
            liability = await repo.deposit_liability(chat_id)
            available = max(0, corp.balance - required_reserve(liability, cfg))
            if available < 1:
                raise BankError("corp_broke")
            amount = max(1, min(amount, credit_limit, available))

            now = _now()
            await repo.upsert_loan(
                chat_id,
                user_id,
                principal=amount,
                roll_debt_principal=0,
                accrued_interest=0,
                opened_at=now,
                due_at=now + cfg.loan_term_days * DAY,
                last_accrual_at=now,
                last_reminded_at=0,
                defaulted=False,
                original_cash_principal=amount,
                credit_limit_at_open=credit_limit,
                rating_eligible=(
                    amount >= cfg.loan_min
                    and amount * 100 >= credit_limit * cfg.credit_reward_min_limit_pct
                ),
            )
            new_size = size + amount
            await players_repo.set_player_fields(chat_id, user_id, size=new_size)
            await repo.corp_apply(chat_id, delta=-amount, reason="loan_open", user_id=user_id)
        await E.log_event(
            chat_id,
            user_id,
            E.LOAN_OPEN,
            delta=amount,
            size_after=new_size,
            meta={
                "principal": amount,
                "due_at": now + cfg.loan_term_days * DAY,
                "credit_limit": credit_limit,
                "rating_eligible": amount >= cfg.loan_min
                and amount * 100 >= credit_limit * cfg.credit_reward_min_limit_pct,
            },
        )
    return OpResult(amount=amount)


async def charge_dick_debt_on_dict(chat_id: int, user_id: int, amount: int) -> tuple[int, bool]:
    """Turn a negative /dick result into a timed receivable.

    The caller already owns the chat lock. No cash is paid to the player and no
    liquid size is removed now; the amount is appended to the existing loan (if
    any) and then follows its interest/default/garnishment machinery.
    Returns ``(due_at, already_defaulted)`` for the result message.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")
    cfg = get_config_sync()
    now = _now()
    proposed_due = now + cfg.dick_debt_term_days * DAY
    loan = await repo.get_loan(chat_id, user_id)
    if loan is None or (loan.principal <= 0 and loan.accrued_interest <= 0):
        await repo.upsert_loan(
            chat_id,
            user_id,
            principal=amount,
            roll_debt_principal=amount,
            accrued_interest=0,
            opened_at=now,
            due_at=proposed_due,
            last_accrual_at=now,
            last_reminded_at=0,
            defaulted=False,
        )
        due_at = proposed_due
        defaulted = False
    else:
        # A measurement charge joins an existing credit without accelerating
        # its already-promised repayment date. With no credit it gets the
        # shorter dedicated term above.
        due_at = int(loan.due_at)
        defaulted = bool(loan.defaulted)
        await repo.upsert_loan(
            chat_id,
            user_id,
            principal=loan.principal + amount,
            roll_debt_principal=loan.roll_debt_principal + amount,
            due_at=due_at,
        )
    await E.log_event(
        chat_id,
        user_id,
        E.DICK_DEBT,
        meta={"amount": amount, "due_at": due_at, "defaulted": defaulted},
    )
    return due_at, defaulted


async def fund_positive_dick(chat_id: int, user_id: int, nominal: int) -> DickPayout:
    """Pay a positive roll from explicit emission plus spendable local cash."""
    if nominal < 0:
        raise ValueError("nominal must be non-negative")
    cfg = get_config_sync()
    emitted = min(nominal, max(0, cfg.dick_emission_cap))
    house_due = nominal - emitted
    async with repo.corp_lock(chat_id):
        corp = await repo.get_corp(chat_id)
        liability = await repo.deposit_liability(chat_id)
        spendable = max(0, corp.balance - required_reserve(liability, cfg))
        corporation_paid = min(house_due, spendable) if corp.status == "healthy" else 0
        await repo.corp_apply(
            chat_id,
            delta=-corporation_paid,
            emission=emitted,
            reason="dick_payout",
            user_id=user_id,
            meta={"nominal": nominal, "emitted": emitted},
        )
    credited = emitted + corporation_paid
    result = DickPayout(
        nominal=nominal,
        credited=credited,
        emitted=emitted,
        corporation_paid=corporation_paid,
        clipped=nominal - credited,
    )
    await E.log_event(
        chat_id,
        user_id,
        E.CORP_EMISSION,
        meta={
            "nominal": nominal,
            "emitted": emitted,
            "corporation_paid": corporation_paid,
            "clipped": result.clipped,
        },
    )
    return result


async def repay_loan(chat_id: int, user_id: int, amount: int | None) -> OpResult:
    """Repay ``amount`` from liquid size (None = as much as possible). Interest
    portion becomes Corporation income; principal portion is burned."""
    cfg = get_config_sync()
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
        await repo.corp_apply(
            chat_id,
            delta=pay,
            interest_earned=interest_part,
            reason="loan_repay",
            user_id=user_id,
            meta={"interest": interest_part},
        )

        remaining = debt - pay
        if remaining <= 0:
            await repo.delete_loan(chat_id, user_id)
            # Paying a fee generated by a bad /dick roll is basic hygiene, not
            # evidence that the Corporation should expand this clown's credit.
            now = _now()
            reward_ready = (
                bool(loan.rating_eligible)
                and loan.original_cash_principal > 0
                and now - loan.opened_at >= cfg.credit_reward_min_age_days * DAY
                and now <= loan.due_at
                and now - (player.last_credit_reward_at if player else 0)
                >= cfg.credit_reward_cooldown_days * DAY
            )
            if reward_ready:
                repaid = (player.loans_repaid if player else 0) + 1
                await players_repo.set_player_fields(
                    chat_id,
                    user_id,
                    loans_repaid=repaid,
                    last_credit_reward_at=now,
                )
        else:
            remaining_roll_debt = max(0, loan.roll_debt_principal - principal_part)
            await repo.upsert_loan(
                chat_id,
                user_id,
                accrued_interest=loan.accrued_interest - interest_part,
                principal=loan.principal - principal_part,
                roll_debt_principal=remaining_roll_debt,
            )
        await E.log_event(
            chat_id,
            user_id,
            E.LOAN_REPAY,
            delta=-pay,
            size_after=new_size,
            meta={
                "amount": pay,
                "principal": principal_part,
                "interest": interest_part,
                "remaining": remaining,
                "cleared": remaining <= 0,
                "voluntary": True,
            },
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
    async with repo.corp_lock(chat_id):
        await repo.corp_apply(
            chat_id,
            delta=take,
            interest_earned=interest_part,
            reason="loan_garnish",
            user_id=user_id,
            meta={"interest": interest_part},
        )
    remaining = debt - take
    if remaining <= 0:
        # Forced recovery on a defaulted loan clears the debt but does NOT count as
        # a clean repayment — only voluntary repay_loan improves credit history.
        await repo.delete_loan(chat_id, user_id)
    else:
        remaining_roll_debt = max(0, loan.roll_debt_principal - principal_part)
        await repo.upsert_loan(
            chat_id,
            user_id,
            accrued_interest=loan.accrued_interest - interest_part,
            principal=loan.principal - principal_part,
            roll_debt_principal=remaining_roll_debt,
        )
    await E.log_event(
        chat_id,
        user_id,
        E.LOAN_GARNISH,
        delta=-take,
        size_after=player_dict["size"],
        meta={
            "amount": take,
            "principal": principal_part,
            "interest": interest_part,
            "remaining": remaining,
            "cleared": remaining <= 0,
            "from_deposit": False,
        },
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
        loan.chat_id,
        loan.user_id,
        accrued_interest=loan.accrued_interest + interest,
        last_accrual_at=loan.last_accrual_at + full_days * DAY,
    )
    if interest:
        await E.log_event(loan.chat_id, loan.user_id, E.LOAN_INTEREST, meta={"interest": interest})
    return interest


async def mark_default(loan) -> None:
    await repo.upsert_loan(loan.chat_id, loan.user_id, defaulted=True)
    player = await players_repo.get_player(loan.chat_id, loan.user_id)
    defaulted_n = (player.loans_defaulted if player else 0) + 1
    await players_repo.set_player_fields(loan.chat_id, loan.user_id, loans_defaulted=defaulted_n)
    await E.log_event(
        loan.chat_id,
        loan.user_id,
        E.LOAN_DEFAULT,
        meta={
            "principal": loan.principal,
            "roll_debt": loan.roll_debt_principal,
            "interest": loan.accrued_interest,
            "total": loan.principal + loan.accrued_interest,
        },
    )


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
        await repo.upsert_deposit(
            loan.chat_id,
            loan.user_id,
            principal=rem_dep,
            interest_remainder_ppm=(dep.interest_remainder_ppm * rem_dep // dep.principal),
        )
    # Cash already in the till; only book the interest slice as earnings.
    async with repo.corp_lock(loan.chat_id):
        await repo.corp_apply(
            loan.chat_id,
            delta=0,
            interest_earned=interest_part,
            reason="deposit_garnish",
            user_id=loan.user_id,
            meta={"amount": take, "interest": interest_part},
        )

    remaining = debt - take
    if remaining <= 0:
        await repo.delete_loan(loan.chat_id, loan.user_id)
    else:
        remaining_roll_debt = max(0, loan.roll_debt_principal - principal_part)
        await repo.upsert_loan(
            loan.chat_id,
            loan.user_id,
            accrued_interest=loan.accrued_interest - interest_part,
            principal=loan.principal - principal_part,
            roll_debt_principal=remaining_roll_debt,
        )
    await E.log_event(
        loan.chat_id,
        loan.user_id,
        E.LOAN_GARNISH,
        delta=-take,
        meta={
            "amount": take,
            "principal": principal_part,
            "interest": interest_part,
            "remaining": remaining,
            "cleared": remaining <= 0,
            "from_deposit": True,
        },
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
    insured, _ = await insured_principal(dep.chat_id, dep.user_id, dep.principal)
    exposed = max(0, dep.principal - insured)
    frac = r.uniform(0, cfg.dep_confisc_max_pct / 100)
    seized = int(exposed * frac)
    if seized <= 0:
        if today:
            await repo.upsert_deposit(dep.chat_id, dep.user_id, last_confisc_day=today)
        return 0
    rem_principal = dep.principal - seized
    await repo.upsert_deposit(
        dep.chat_id,
        dep.user_id,
        principal=rem_principal,
        interest_remainder_ppm=(dep.interest_remainder_ppm * rem_principal // dep.principal),
        last_confisc_day=today,
    )
    # The seized cash is already sitting in the till (deposits fund it). We only
    # shrink the depositor's claim and book it as house earnings — no cash moves.
    await repo.corp_apply(
        dep.chat_id,
        delta=0,
        penalties=seized,
        reason="confiscation",
        user_id=dep.user_id,
        meta={"seized": seized},
    )
    await E.log_event(
        dep.chat_id, dep.user_id, E.CONFISCATION, delta=-seized, meta={"seized": seized}
    )
    return seized


# --------------------------------------------------------------------------- #
# Corporation read.
# --------------------------------------------------------------------------- #


async def corp_state(chat_id: int) -> CorpView:
    cfg = get_config_sync()
    corp = await repo.get_corp(chat_id)
    deposits = await repo.deposit_liability(chat_id)
    reserve = required_reserve(deposits, cfg)
    return CorpView(
        chat_id=chat_id,
        balance=corp.balance,
        insurance_reserve=corp.insurance_reserve,
        status=corp.status,
        sanation_deadline=corp.sanation_deadline,
        bankruptcy_count=corp.bankruptcy_count,
        deposits=deposits,
        reserve_required=reserve,
        spendable=max(0, corp.balance - reserve) if corp.status == "healthy" else 0,
        total_tax=corp.total_tax,
        total_interest_earned=corp.total_interest_earned,
        total_interest_paid=corp.total_interest_paid,
        total_penalties=corp.total_penalties,
        total_poker_rake=corp.total_poker_rake,
        total_emission=corp.total_emission,
        total_bailin=corp.total_bailin,
    )


async def credit_corp_tax(chat_id: int, user_id: int, amount: int) -> None:
    """Funnel the duel house-cut into the Corporation (previously it vanished)."""
    if amount <= 0:
        return
    await repo.corp_apply(chat_id, delta=amount, tax=amount, reason="duel_tax", user_id=user_id)
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
    except TelegramAPIError:
        # The debtor may never have opened a DM with the bot; skip silently and
        # try again next cycle (the cooldown flag is only armed on a real send).
        return
    await repo.upsert_loan(loan.chat_id, loan.user_id, last_reminded_at=now)


async def run_collector_pass(bot) -> None:
    """One sweep: grow loan interest, default the overdue, nag debtors in DM, and
    roll deposit confiscations. Each step is best-effort and independent."""
    cfg = get_config_sync()
    now = _now()
    today = datetime.now(UTC).date().isoformat()

    for loan in await repo.all_loans():
        fresh = None
        async with players_repo.get_chat_lock(loan.chat_id):
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
                # A deposit is no shelter from a defaulted debt.
                await recover_from_deposit(fresh)
                fresh = await repo.get_loan(loan.chat_id, loan.user_id)
        if fresh is not None and fresh.defaulted:
            await _maybe_remind(bot, fresh, cfg, now)

    for dep in await repo.all_deposits():
        async with players_repo.get_chat_lock(dep.chat_id):
            fresh = await repo.get_deposit(dep.chat_id, dep.user_id)
            corp = await repo.get_corp(dep.chat_id)
            if fresh is not None and fresh.principal > 0 and corp.status == "healthy":
                await roll_confiscation(fresh, cfg, today)

    await _run_corporation_crises(bot, now)


async def _insured_total(chat_id: int, deposits: list) -> int:
    total = 0
    for dep in deposits:
        covered, _ = await insured_principal(chat_id, dep.user_id, dep.principal)
        total += covered
    return total


async def _bail_in(chat_id: int, now: int) -> int:
    """Wipe accrued and uninsured deposit claims without minting cash."""
    wiped = 0
    deposits = await repo.chat_deposits(chat_id)
    for dep in deposits:
        covered, _ = await insured_principal(chat_id, dep.user_id, dep.principal, now)
        wiped += dep.accrued + max(0, dep.principal - covered)
        if covered <= 0:
            await repo.delete_deposit(chat_id, dep.user_id)
            await repo.delete_insurance_for_deposit(chat_id, dep.user_id)
        else:
            await repo.upsert_deposit(
                chat_id,
                dep.user_id,
                principal=covered,
                accrued=0,
                interest_remainder_ppm=0,
            )
    corp = await repo.get_corp(chat_id)
    preserved = await repo.deposit_liability(chat_id)
    status = "healthy" if preserved <= corp.balance + corp.insurance_reserve else "recovery"
    await repo.set_corp_fields(
        chat_id,
        status=status,
        sanation_started_at=0,
        sanation_deadline=0,
        bankruptcy_count=corp.bankruptcy_count + 1,
    )
    await repo.corp_apply(
        chat_id,
        delta=0,
        bailin=wiped,
        reason="bailin",
        meta={"wiped": wiped, "preserved": preserved, "status": status},
    )
    await E.log_event(chat_id, 0, E.CORP_BAILIN, meta={"wiped": wiped, "preserved": preserved})
    return wiped


async def _run_corporation_crises(bot, now: int) -> None:
    """Advance local seven-day sanctions and send one crude daily pressure wave."""
    import html

    import texts
    from repositories import threads as threads_repo

    for corp in await repo.all_corps():
        if corp.status == "healthy":
            continue
        deposits = await repo.chat_deposits(corp.chat_id)
        liability = sum(d.principal + d.accrued for d in deposits)
        insured = await _insured_total(corp.chat_id, deposits)
        uninsured = max(0, liability - insured)
        can_cover = corp.balance >= uninsured and corp.balance + corp.insurance_reserve >= liability
        if can_cover:
            await repo.set_corp_fields(
                corp.chat_id, status="healthy", sanation_started_at=0, sanation_deadline=0
            )
            continue
        if corp.status == "sanation" and now >= corp.sanation_deadline:
            await _bail_in(corp.chat_id, now)
            continue
        if now - corp.last_crisis_notice_at < DAY:
            continue
        loans = await repo.chat_loans(corp.chat_id)
        lines = []
        for loan in loans:
            debt = loan.principal + loan.accrued_interest
            if debt <= 0:
                continue
            player = await players_repo.get_player(corp.chat_id, loan.user_id)
            name = html.escape(player.name if player and player.name else str(loan.user_id))
            lines.append(f'• <a href="tg://user?id={loan.user_id}">{name}</a>: {debt} см')
            try:
                await bot.send_message(
                    loan.user_id,
                    texts.crisis_debtor_reminder(debt, corp.sanation_deadline),
                    parse_mode="HTML",
                )
            except TelegramAPIError:
                pass
        text = texts.crisis_chat_summary(corp.sanation_deadline, liability, lines)
        try:
            thread_id, _reason = await threads_repo.resolve_thread(corp.chat_id)
            if corp.crisis_message_id:
                await bot.edit_message_text(
                    text,
                    chat_id=corp.chat_id,
                    message_id=corp.crisis_message_id,
                    parse_mode="HTML",
                )
            else:
                message = await bot.send_message(
                    corp.chat_id, text, parse_mode="HTML", message_thread_id=thread_id
                )
                await repo.set_corp_fields(
                    corp.chat_id,
                    crisis_message_id=message.message_id,
                    crisis_thread_id=thread_id or 0,
                )
        except TelegramAPIError:
            pass
        await repo.set_corp_fields(corp.chat_id, last_crisis_notice_at=now)
