"""Economy core: deposits, loans and chat-local Corporation accounts.

Money rules (kept deliberately simple but internally consistent):

* Deposit principal is **moved** out of the player's liquid ``size`` (it freezes:
  included in net worth but unusable in duels and /dick growth). Interest is paid **by the
  Corporation** (its balance shrinks) and accrues only on days the owner actually
  plays /dick. The effective rate decays per active day and total yield is capped,
  so "deposit and forget" never pays off. Early withdrawal forfeits accrued
  interest and pays a penalty to the Corporation; deposits can also be randomly
  (partially) confiscated by the Corporation.
* Loan principal leaves the Corporation till and becomes liquid ``size``. A
  repayment refills the till; its interest slice is house profit. Interest grows
  the debt by calendar time and, when repaid or garnished, becomes Corporation
  income. Past the due date the loan defaults and is recovered from /dick gains,
  duel winnings, and deposit principal regardless of either protection layer.
* Every group has an isolated Corporation. A 25% liquidity reserve limits risk.
  The first configured slice of each principal is protected without a policy;
  active SЕКАСКО applies on top. A withdrawal the till cannot fund immediately
  writes every claim down to those protected layers, pays the initiator from the
  surviving principal (the till may go negative), and leaves new risk operations
  frozen in recovery while preserved claims remain withdrawable. Accrued interest
  and overdue-loan recovery are never protected.

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
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import get_session_factory
from db.models import Deposit, DepositInsurance, Loan
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
class SekaskoView:
    active: int
    base_limit: int
    base_protected: int
    sekasko_protected: int
    total_protected: int
    risky: int
    unused: int
    configured_limit: int
    purchase_limit: int
    limit_available: int
    available: int
    premium_pct: int
    available_premium: int
    next_expires_at: int
    next_expiring: int


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
    sekasko: SekaskoView
    loan: LoanView | None
    loans_repaid: int
    loans_defaulted: int
    loan_limit: int
    pisyago: PisyagoView
    next_credit_reward_at: int = 0


@dataclass(frozen=True)
class DepositProtection:
    base_limit: int
    base: int
    sekasko: int
    total: int
    risky: int
    unused: int
    issuance_limit: int
    issuance_available: int


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
    amount, expiry, _ = _sekasko_coverage_schedule(policies)
    protection = deposit_protection(principal, amount, get_config_sync())
    return protection.total, expiry


def sekasko_premium(amount: int, premium_pct: int) -> int:
    """Return the premium charged for a new amount of deposit coverage."""
    if amount <= 0:
        return 0
    return max(1, (amount * max(0, premium_pct) + 99) // 100)


def _affordable_sekasko(size: int, premium_pct: int, limit: int) -> int:
    """Maximum coverage whose rounded-up premium fits the liquid balance."""
    if size < 1 or limit <= 0:
        return 0
    if premium_pct <= 0:
        return limit
    return min(limit, size * 100 // premium_pct)


def _sekasko_coverage_schedule(policies: list) -> tuple[int, int, int]:
    """Return active coverage and the first tranche that expires.

    The configured limit controls new issuance only: lowering it must not
    retroactively devalue an existing purchased or seasonal policy.
    """
    by_expiry: dict[int, int] = {}
    for policy in policies:
        amount = max(0, int(policy.amount))
        if amount:
            expires_at = int(policy.expires_at)
            by_expiry[expires_at] = by_expiry.get(expires_at, 0) + amount
    raw_total = sum(by_expiry.values())
    if not by_expiry:
        return 0, 0, 0
    expires_at = min(by_expiry)
    return raw_total, expires_at, by_expiry[expires_at]


def _risk_free_principal(cfg: GlobalConfig) -> int:
    # The migration/config worker adds the field. The fallback keeps this task
    # branch executable before those independently owned changes are integrated.
    return max(0, int(getattr(cfg, "dep_risk_free_principal", 50)))


def deposit_protection(principal: int, active_sekasko: int, cfg: GlobalConfig) -> DepositProtection:
    principal = max(0, int(principal))
    active_sekasko = max(0, int(active_sekasko))
    base_limit = _risk_free_principal(cfg)
    base = min(principal, base_limit)
    principal_above_base = max(0, principal - base)
    sekasko = min(active_sekasko, principal_above_base)
    total = base + sekasko
    issuance_limit = min(max(0, principal - base_limit), max(0, int(cfg.sekasko_max_coverage)))
    return DepositProtection(
        base_limit=base_limit,
        base=base,
        sekasko=sekasko,
        total=total,
        risky=max(0, principal - total),
        unused=max(0, active_sekasko - principal_above_base),
        issuance_limit=issuance_limit,
        issuance_available=max(0, issuance_limit - active_sekasko),
    )


def required_reserve(liability: int, cfg: GlobalConfig) -> int:
    return max(0, liability) * cfg.corp_liquidity_reserve_pct // 100


async def get_summary(chat_id: int, user_id: int) -> BankSummary:
    cfg = get_config_sync()
    player = await players_repo.get_player(chat_id, user_id)
    size = player.size if player else 0
    repaid = player.loans_repaid if player else 0
    defaulted_n = player.loans_defaulted if player else 0

    now = _now()
    policies = await repo.active_insurance(chat_id, user_id, now)
    configured_limit = max(0, cfg.sekasko_max_coverage)
    active_coverage, next_expires_at, next_expiring = _sekasko_coverage_schedule(policies)

    dep_row = await repo.get_deposit(chat_id, user_id)
    dep_view = None
    principal = max(0, int(dep_row.principal)) if dep_row is not None else 0
    protection = deposit_protection(principal, active_coverage, cfg)
    if dep_row is not None and principal > 0:
        dep_view = DepositView(
            principal=principal,
            accrued=dep_row.accrued,
            matures_at=dep_row.matures_at,
            matured=now >= dep_row.matures_at,
            active_days=dep_row.active_days_count,
            insured=protection.sekasko,
            insurance_expires_at=next_expires_at,
        )

    purchase_limit = protection.issuance_limit
    limit_available = protection.issuance_available
    available = _affordable_sekasko(size, cfg.sekasko_premium_pct, limit_available)
    sekasko = SekaskoView(
        active=active_coverage,
        base_limit=protection.base_limit,
        base_protected=protection.base,
        sekasko_protected=protection.sekasko,
        total_protected=protection.total,
        risky=protection.risky,
        unused=protection.unused,
        configured_limit=configured_limit,
        purchase_limit=purchase_limit,
        limit_available=limit_available,
        available=available,
        premium_pct=max(0, cfg.sekasko_premium_pct),
        available_premium=sekasko_premium(available, cfg.sekasko_premium_pct),
        next_expires_at=next_expires_at,
        next_expiring=next_expiring,
    )

    poker_stack = await poker_repo.get_money_stack(chat_id, user_id)
    deposit_assets = (
        dep_row.principal + dep_row.accrued if dep_row is not None and dep_row.principal > 0 else 0
    )
    assets = max(0, size) + deposit_assets + poker_stack
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
        sekasko=sekasko,
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
class BailInInfo:
    wiped: int
    protected_claims: int
    payout: int
    balance: int
    deficit: int
    status: str


@dataclass
class OpResult:
    amount: int
    extra: int = 0  # penalty / interest / forfeit, depending on the op
    bail_in: BailInInfo | None = None


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
        factory = get_session_factory()
        async with factory() as session, session.begin():
            corp = await repo.ensure_corp_in(session, chat_id)
            if corp.status != "healthy":
                raise BankError("corp_frozen")
            dep = await session.get(Deposit, (chat_id, user_id))
            if dep is None or dep.principal <= 0:
                raise BankError("no_deposit")
            insured = await session.scalar(
                select(func.coalesce(func.sum(DepositInsurance.amount), 0)).where(
                    DepositInsurance.chat_id == chat_id,
                    DepositInsurance.user_id == user_id,
                    DepositInsurance.expires_at > _now(),
                )
            )
            protection = deposit_protection(dep.principal, int(insured or 0), cfg)
            available = protection.issuance_available
            if amount < 1 or amount > available:
                raise BankError("insurance_limit")
            premium = sekasko_premium(amount, cfg.sekasko_premium_pct)
            player = await players_repo.get_player_in(session, chat_id, user_id)
            if player is None or player.size < premium:
                raise BankError("insurance_cash")
            player.size -= premium
            expires_at = _now() + cfg.dep_term_days * DAY
            session.add(
                DepositInsurance(
                    chat_id=chat_id,
                    user_id=user_id,
                    amount=amount,
                    premium=premium,
                    expires_at=expires_at,
                )
            )
            await repo.corp_apply_in(
                session,
                chat_id,
                delta=0,
                insurance_delta=premium,
                reason="sekasko_premium",
                user_id=user_id,
                meta={"insured": amount, "expires_at": expires_at},
            )
            E.add_event(
                session,
                chat_id,
                user_id,
                E.DEPOSIT_INSURANCE,
                delta=-premium,
                size_after=player.size,
                meta={"insured": amount, "premium": premium, "expires_at": expires_at},
            )
            return OpResult(amount=amount, extra=premium)


def _purchased_insurance_filter(chat_id: int, user_id: int):
    """Select disposable policies while preserving active seasonal prizes.

    ``source`` is supplied by the season-prize migration. Keeping the fallback
    makes rolling code upgrades safe before that migration is present.
    """
    clauses = [
        DepositInsurance.chat_id == chat_id,
        DepositInsurance.user_id == user_id,
    ]
    source = getattr(DepositInsurance, "source", None)
    if source is not None:
        clauses.append(or_(source != "season_prize", DepositInsurance.expires_at <= _now()))
    return clauses


async def _active_insurance_amounts_in(
    session: AsyncSession, chat_id: int, user_ids: list[int], now: int
) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(
                DepositInsurance.user_id,
                func.coalesce(func.sum(DepositInsurance.amount), 0),
            )
            .where(
                DepositInsurance.chat_id == chat_id,
                DepositInsurance.user_id.in_(user_ids),
                DepositInsurance.expires_at > now,
            )
            .group_by(DepositInsurance.user_id)
        )
    ).all()
    return {int(user_id): int(amount) for user_id, amount in rows}


async def _bail_in_in(
    session: AsyncSession,
    chat_id: int,
    now: int,
    cfg: GlobalConfig,
    *,
    initiator_user_id: int = 0,
) -> BailInInfo:
    """Atomically reduce every claim to base + active SЕКАСКО protection."""
    corp = await repo.ensure_corp_in(session, chat_id)
    deposits = list(
        (await session.execute(select(Deposit).where(Deposit.chat_id == chat_id))).scalars()
    )
    insured = await _active_insurance_amounts_in(
        session, chat_id, [dep.user_id for dep in deposits], now
    )
    wiped = 0
    protected_claims = 0
    for dep in deposits:
        protection = deposit_protection(dep.principal, insured.get(dep.user_id, 0), cfg)
        wiped += max(0, int(dep.accrued)) + protection.risky
        protected_claims += protection.total
        if protection.total <= 0:
            await session.delete(dep)
            await session.execute(
                delete(DepositInsurance).where(*_purchased_insurance_filter(chat_id, dep.user_id))
            )
        else:
            dep.principal = protection.total
            dep.accrued = 0
            dep.interest_remainder_ppm = 0

    deficit = max(
        0,
        -int(corp.balance),
        protected_claims - (int(corp.balance) + int(corp.insurance_reserve)),
    )
    status = "recovery" if deficit > 0 else "healthy"
    corp.status = status
    corp.sanation_started_at = 0
    corp.sanation_deadline = 0
    return BailInInfo(
        wiped=wiped,
        protected_claims=protected_claims,
        payout=0,
        balance=int(corp.balance),
        deficit=deficit,
        status=status,
    )


async def _record_bail_in_in(
    session: AsyncSession,
    chat_id: int,
    info: BailInInfo,
    *,
    initiator_user_id: int = 0,
    force: bool = False,
) -> None:
    """Book one global bail-in after its final payout state is known."""
    if info.wiped <= 0 and not force:
        return
    corp = await repo.ensure_corp_in(session, chat_id)
    corp.bankruptcy_count += 1
    meta = {
        "wiped": info.wiped,
        "protected": info.protected_claims,
        "payout": info.payout,
        "balance": info.balance,
        "deficit": info.deficit,
        "status": info.status,
        "initiator_user_id": initiator_user_id,
    }
    await repo.corp_apply_in(
        session,
        chat_id,
        delta=0,
        bailin=info.wiped,
        reason="bailin",
        user_id=initiator_user_id,
        meta=meta,
    )
    E.add_event(session, chat_id, 0, E.CORP_BAILIN, meta=meta)


async def open_deposit(chat_id: int, user_id: int, amount: int) -> OpResult:
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id), repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            corp = await repo.ensure_corp_in(session, chat_id)
            if corp.status != "healthy":
                raise BankError("corp_frozen")
            player = await players_repo.get_player_in(session, chat_id, user_id)
            if player is None or player.size <= 0:
                raise BankError("no_size")
            amount = max(1, min(amount, player.size))
            dep = await session.get(Deposit, (chat_id, user_id))
            now = _now()
            matures_at = now + cfg.dep_term_days * DAY
            if dep is not None and dep.principal > 0:
                dep.principal += amount
                dep.matures_at = max(dep.matures_at, matures_at)
            else:
                await repo.upsert_deposit_in(
                    session,
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
            player.size -= amount
            await repo.corp_apply_in(
                session, chat_id, delta=amount, reason="deposit_open", user_id=user_id
            )
            E.add_event(
                session,
                chat_id,
                user_id,
                E.DEPOSIT_OPEN,
                delta=-amount,
                size_after=player.size,
            )
    return OpResult(amount=amount)


async def withdraw_deposit(chat_id: int, user_id: int, amount: int | None) -> OpResult:
    """Withdraw ``amount`` of principal (None = all). Returns the credited amount
    and the penalty+forfeited interest withheld by the Corporation.

    A healthy Corporation that cannot fund the withdrawal and its remaining
    reserve performs one atomic immediate bail-in first. Recovery withdrawals
    never trigger another bail-in or reserve check.
    """
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id), repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            corp = await repo.ensure_corp_in(session, chat_id)
            if corp.status not in {"healthy", "recovery"}:
                raise BankError("corp_frozen")
            dep = await session.get(Deposit, (chat_id, user_id))
            if dep is None or dep.principal <= 0:
                raise BankError("no_deposit")

            requested = dep.principal if amount is None else max(1, min(amount, dep.principal))
            w = requested
            accrued_share = dep.accrued * w // dep.principal
            matured = _now() >= dep.matures_at
            penalty = 0 if matured else (w * cfg.dep_early_penalty_pct + 99) // 100
            credited = w + accrued_share if matured else max(0, w - penalty)

            bail_in = None
            if corp.status == "healthy":
                liability = await repo.deposit_liability_in(session, chat_id)
                remaining_liability = max(0, liability - w - accrued_share)
                if corp.balance - credited < required_reserve(remaining_liability, cfg):
                    bail_in = await _bail_in_in(
                        session,
                        chat_id,
                        _now(),
                        cfg,
                        initiator_user_id=user_id,
                    )
                    dep = await session.get(Deposit, (chat_id, user_id))
                    if dep is None or dep.principal <= 0:
                        w = 0
                        accrued_share = 0
                        matured = True
                        penalty = 0
                        credited = 0
                    else:
                        w = min(requested, dep.principal)
                        accrued_share = dep.accrued * w // dep.principal
                        matured = _now() >= dep.matures_at
                        penalty = 0 if matured else (w * cfg.dep_early_penalty_pct + 99) // 100
                        credited = w + accrued_share if matured else max(0, w - penalty)

            player = await players_repo.ensure_player_in(session, chat_id, user_id)
            player.size += credited
            new_size = player.size
            if dep is not None and dep.principal > 0:
                rem_principal = dep.principal - w
                rem_accrued = dep.accrued - accrued_share
                rem_remainder = dep.interest_remainder_ppm * rem_principal // dep.principal
                if rem_principal <= 0:
                    await session.delete(dep)
                    await session.execute(
                        delete(DepositInsurance).where(
                            *_purchased_insurance_filter(chat_id, user_id)
                        )
                    )
                else:
                    dep.principal = rem_principal
                    dep.accrued = max(0, rem_accrued)
                    dep.interest_remainder_ppm = max(0, rem_remainder)

            await session.flush()
            remaining_liability = await repo.deposit_liability_in(session, chat_id)
            final_balance = int(corp.balance) - credited
            deficit = max(
                0,
                -final_balance,
                remaining_liability - (final_balance + int(corp.insurance_reserve)),
            )
            if bail_in is not None or corp.status == "recovery":
                corp.status = "recovery" if deficit > 0 else "healthy"
                corp.sanation_started_at = 0
                corp.sanation_deadline = 0

            bail_meta = None
            if bail_in is not None:
                bail_in.payout = credited
                bail_in.balance = final_balance
                bail_in.deficit = deficit
                bail_in.status = corp.status
                bail_meta = {
                    "wiped": bail_in.wiped,
                    "protected": bail_in.protected_claims,
                    "payout": credited,
                    "balance": final_balance,
                    "deficit": deficit,
                    "status": corp.status,
                }

            withdraw_meta = {
                "matured": matured,
                "penalty": penalty,
                "interest": accrued_share,
                "requested": requested,
                "principal_withdrawn": w,
                "payout": credited,
                "balance": final_balance,
                "deficit": deficit,
                "bail_in": bail_meta,
            }
            await repo.corp_apply_in(
                session,
                chat_id,
                delta=-credited,
                penalties=0 if matured else penalty + accrued_share,
                reason="deposit_withdraw",
                user_id=user_id,
                meta=withdraw_meta,
            )
            if bail_in is not None:
                await _record_bail_in_in(
                    session,
                    chat_id,
                    bail_in,
                    initiator_user_id=user_id,
                    force=True,
                )
            if not matured and (penalty or accrued_share):
                E.add_event(
                    session,
                    chat_id,
                    user_id,
                    E.DEPOSIT_PENALTY,
                    meta={
                        "penalty": penalty,
                        "forfeit_interest": accrued_share,
                        "bail_in": bail_meta,
                    },
                )
            E.add_event(
                session,
                chat_id,
                user_id,
                E.DEPOSIT_WITHDRAW,
                delta=credited,
                size_after=new_size,
                meta=withdraw_meta,
            )

    return OpResult(
        amount=credited,
        extra=penalty + (0 if matured else accrued_share),
        bail_in=bail_in,
    )


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
    async with players_repo.get_chat_lock(chat_id), repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            existing = await session.get(Loan, (chat_id, user_id))
            if existing is not None and (existing.principal > 0 or existing.accrued_interest > 0):
                raise BankError("loan_exists")
            player = await players_repo.ensure_player_in(session, chat_id, user_id)
            credit_limit = max_loan(player.size, player.loans_repaid, player.loans_defaulted, cfg)
            if credit_limit < 1:
                cooldown.touch(chat_id, user_id, _LOAN_DENY_KEY)
                raise BankError("no_credit")
            corp = await repo.ensure_corp_in(session, chat_id)
            if corp.status != "healthy":
                raise BankError("corp_frozen")
            liability = await repo.deposit_liability_in(session, chat_id)
            available = max(0, corp.balance - required_reserve(liability, cfg))
            if available < 1:
                raise BankError("corp_broke")
            amount = max(1, min(amount, credit_limit, available))
            now = _now()
            eligible = amount >= cfg.loan_min and (
                amount * 100 >= credit_limit * cfg.credit_reward_min_limit_pct
            )
            await repo.upsert_loan_in(
                session,
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
                rating_eligible=eligible,
            )
            player.size += amount
            new_size = player.size
            await repo.corp_apply_in(
                session, chat_id, delta=-amount, reason="loan_open", user_id=user_id
            )
            E.add_event(
                session,
                chat_id,
                user_id,
                E.LOAN_OPEN,
                delta=amount,
                size_after=new_size,
                meta={
                    "principal": amount,
                    "due_at": now + cfg.loan_term_days * DAY,
                    "credit_limit": credit_limit,
                    "rating_eligible": eligible,
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
    async with players_repo.get_chat_lock(chat_id), repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            loan = await session.get(Loan, (chat_id, user_id))
            if loan is None or (loan.principal <= 0 and loan.accrued_interest <= 0):
                raise BankError("no_loan")
            player = await players_repo.ensure_player_in(session, chat_id, user_id)
            if player.size <= 0:
                raise BankError("no_size")
            debt = loan.principal + loan.accrued_interest
            pay = debt if amount is None else amount
            pay = max(1, min(pay, player.size, debt))
            interest_part = min(pay, loan.accrued_interest)
            principal_part = pay - interest_part
            player.size -= pay
            new_size = player.size
            await repo.corp_apply_in(
                session,
                chat_id,
                delta=pay,
                interest_earned=interest_part,
                reason="loan_repay",
                user_id=user_id,
                meta={"interest": interest_part},
            )
            remaining = debt - pay
            if remaining <= 0:
                now = _now()
                reward_ready = (
                    bool(loan.rating_eligible)
                    and loan.original_cash_principal > 0
                    and now - loan.opened_at >= cfg.credit_reward_min_age_days * DAY
                    and now <= loan.due_at
                    and now - player.last_credit_reward_at >= cfg.credit_reward_cooldown_days * DAY
                )
                await session.delete(loan)
                if reward_ready:
                    player.loans_repaid += 1
                    player.last_credit_reward_at = now
            else:
                loan.accrued_interest -= interest_part
                loan.principal -= principal_part
                loan.roll_debt_principal = max(0, loan.roll_debt_principal - principal_part)
            E.add_event(
                session,
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
    if cfg.dep_confisc_chance_pct <= 0:
        return 0
    r = rng or random
    async with players_repo.get_chat_lock(dep.chat_id), repo.corp_lock(dep.chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            row = await session.get(Deposit, (dep.chat_id, dep.user_id))
            if row is None or row.principal <= 0:
                return 0
            corp = await repo.ensure_corp_in(session, dep.chat_id)
            if corp.status != "healthy":
                return 0
            if today and row.last_confisc_day == today:
                return 0

            fired = r.random() < cfg.dep_confisc_chance_pct / 100
            # Consume the day's roll even on a miss, in the same transaction as
            # every possible claim/counter/event mutation.
            if today:
                row.last_confisc_day = today
            if not fired:
                return 0

            insured = await _active_insurance_amounts_in(
                session, dep.chat_id, [dep.user_id], _now()
            )
            protection = deposit_protection(row.principal, insured.get(dep.user_id, 0), cfg)
            frac = r.uniform(0, cfg.dep_confisc_max_pct / 100)
            seized = int(protection.risky * frac)
            if seized <= 0:
                return 0

            rem_principal = row.principal - seized
            row.interest_remainder_ppm = row.interest_remainder_ppm * rem_principal // row.principal
            row.principal = rem_principal
            await repo.corp_apply_in(
                session,
                dep.chat_id,
                delta=0,
                penalties=seized,
                reason="confiscation",
                user_id=dep.user_id,
                meta={"seized": seized, "protected": protection.total},
            )
            E.add_event(
                session,
                dep.chat_id,
                dep.user_id,
                E.CONFISCATION,
                delta=-seized,
                meta={"seized": seized, "protected": protection.total},
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
        await roll_confiscation(dep, cfg, today)

    await _run_corporation_crises(bot, now)


async def _bail_in(chat_id: int, now: int) -> BailInInfo:
    """Transactional wrapper for collector resolution of a legacy sanation."""
    cfg = get_config_sync()
    async with players_repo.get_chat_lock(chat_id), repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            info = await _bail_in_in(session, chat_id, now, cfg)
            await _record_bail_in_in(session, chat_id, info, force=True)
            return info


async def _run_corporation_crises(bot, now: int) -> None:
    """Resolve legacy sanation and recovery states without deadline reminders."""
    import texts
    from repositories import threads as threads_repo

    cfg = get_config_sync()
    for snapshot in await repo.all_corps():
        if snapshot.status == "healthy":
            continue
        legacy_info = None
        async with players_repo.get_chat_lock(snapshot.chat_id), repo.corp_lock(snapshot.chat_id):
            factory = get_session_factory()
            async with factory() as session, session.begin():
                corp = await repo.ensure_corp_in(session, snapshot.chat_id)
                if corp.status == "sanation":
                    legacy_info = await _bail_in_in(session, snapshot.chat_id, now, cfg)
                    await _record_bail_in_in(session, snapshot.chat_id, legacy_info, force=True)
                elif corp.status == "recovery":
                    liability = await repo.deposit_liability_in(session, snapshot.chat_id)
                    deficit = max(
                        0,
                        -int(corp.balance),
                        liability - (int(corp.balance) + int(corp.insurance_reserve)),
                    )
                    if deficit <= 0:
                        corp.status = "healthy"
                        corp.sanation_started_at = 0
                        corp.sanation_deadline = 0
        if legacy_info is not None:
            try:
                thread_id, _reason = await threads_repo.resolve_thread(snapshot.chat_id)
                await bot.send_message(
                    snapshot.chat_id,
                    texts.bank_legacy_bail_in_notice(
                        legacy_info.wiped,
                        legacy_info.protected_claims,
                        legacy_info.balance,
                        legacy_info.deficit,
                    ),
                    parse_mode="HTML",
                    message_thread_id=thread_id,
                )
            except TelegramAPIError:
                pass
