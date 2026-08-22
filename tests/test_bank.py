"""Unit tests for the deposit/loan/Corporation economy (services/bank.py).

Pure helpers are tested directly; the async ops run against a throwaway SQLite
file (one per test) so money conservation and the Corporation balance can be
asserted end-to-end. The global config is left at its defaults (the cache is
empty, so ``get_config_sync`` returns ``_defaults()``).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.global_settings import GlobalConfig, _defaults


def cfg() -> GlobalConfig:
    return _defaults()


# --------------------------------------------------------------------------- #
# Pure helpers — no DB.
# --------------------------------------------------------------------------- #


def test_effective_rate_decays_to_floor():
    from services import bank

    c = cfg()
    r0 = bank.effective_deposit_rate(0, c)
    r1 = bank.effective_deposit_rate(1, c)
    assert r0 == pytest.approx(c.dep_rate_pct / 100)
    assert r1 < r0  # decays with each active day
    r_far = bank.effective_deposit_rate(100, c)
    assert r_far == pytest.approx(c.dep_rate_floor_pct / 100)  # never below floor


def test_deposit_day_interest_respects_cap():
    from services import bank

    c = cfg()
    principal = 1000
    cap_total = principal * c.dep_yield_cap_pct // 100
    # Already at the cap → no further interest.
    assert bank.deposit_day_interest(principal, cap_total, 0, c) == 0
    # Near the cap → only the remaining headroom is paid.
    near = cap_total - 5
    assert bank.deposit_day_interest(principal, near, 0, c) == 5
    # Idle/zero principal never earns.
    assert bank.deposit_day_interest(0, 0, 0, c) == 0


def test_small_deposit_carries_fraction_without_free_minimum():
    from services import bank

    c = cfg()
    principal = 20
    accrued = 0
    remainder = 0
    paid = []
    for day in range(4):
        interest, remainder = bank.deposit_day_credit(principal, accrued, day, remainder, c)
        paid.append(interest)
        accrued += interest
    assert paid[:3] == [0, 0, 0]
    assert paid[3] == 1
    assert accrued == principal * c.dep_yield_cap_pct // 100
    assert remainder == 0


def test_rebalanced_bank_defaults_help_newcomers():
    c = cfg()
    assert (c.dep_rate_pct, c.dep_rate_decay_pct, c.dep_rate_floor_pct) == (2, 25, 0)
    assert c.dep_yield_cap_pct == 8
    assert (c.loan_rate_pct, c.loan_min, c.loan_term_days) == (2, 15, 7)


def test_credit_multiplier_clamped():
    from services import bank

    assert bank.credit_multiplier(0, 0) == 1.0
    assert bank.credit_multiplier(4, 0) == 2.0
    assert bank.credit_multiplier(0, 2) == 0.0  # clamped at 0
    assert bank.credit_multiplier(100, 0) == 3.0  # clamped at 3


def test_max_loan_scales_with_size_and_history():
    from services import bank

    c = cfg()
    base = bank.max_loan(1000, 0, 0, c)
    assert base == 1000 * c.loan_max_base_pct // 100
    assert bank.max_loan(1000, 4, 0, c) == base * 2  # good history doubles
    assert bank.max_loan(1000, 0, 2, c) == 0  # defaults zero out credit
    # A broke (size 0) but un-defaulted player still gets the starter floor.
    assert bank.max_loan(0, 5, 0, c) == c.loan_min
    assert bank.max_loan(0, 0, 2, c) == 0  # but heavy defaulters stay shut out


def test_loan_interest_accrued():
    from services import bank

    c = cfg()
    expected = int(100 * (c.loan_rate_pct / 100) * 3)
    assert bank.loan_interest_accrued(100, 3, c) == expected
    assert bank.loan_interest_accrued(100, 0, c) == 0


def test_catastrophic_range_is_only_minus_179():
    from services.game import WEIGHTED_RANGES

    assert ((-179, -179), 0.0001) in WEIGHTED_RANGES
    assert all(not (lo <= -178 <= hi) for (lo, hi), _weight in WEIGHTED_RANGES)


@pytest.mark.parametrize(
    ("assets", "expected"),
    [
        (0, 100),
        (4, 100),
        (5, 75),
        (9, 75),
        (10, 50),
        (14, 50),
        (15, 25),
        (19, 25),
        (20, 0),
        (100, 0),
    ],
)
def test_pisyago_coverage_tiers(assets: int, expected: int):
    from services import bank

    assert bank.pisyago_coverage_pct(assets, threshold=20) == expected


# --------------------------------------------------------------------------- #
# DB-backed async ops.
# --------------------------------------------------------------------------- #


@pytest.fixture
async def db():
    """Fresh SQLite file + engine per test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    from db import engine as engine_mod

    await engine_mod.dispose_engine()  # drop any engine bound to another path
    await engine_mod.init_db()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        os.unlink(path)


CHAT, USER = -1001, 42


async def _seed_player(size: int, **fields):
    from repositories import players as players_repo

    await players_repo.set_player_fields(CHAT, USER, size=size, **fields)


async def test_open_deposit_moves_size(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)
    res = await bank.open_deposit(CHAT, USER, 40)
    assert res.amount == 40

    from repositories import players as players_repo

    player = await players_repo.get_player(CHAT, USER)
    assert player.size == 60
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.principal == 40


async def test_open_deposit_rolls_back_every_accounting_leg(db, monkeypatch):
    from sqlalchemy import func, select

    from db.engine import get_session_factory
    from db.models import CorporationLedger
    from repositories import bank as repo
    from repositories import events as events_repo
    from repositories import players as players_repo
    from services import bank

    await _seed_player(100)

    def explode(*_args, **_kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(events_repo, "add_event", explode)
    with pytest.raises(RuntimeError, match="injected"):
        await bank.open_deposit(CHAT, USER, 40)

    assert (await players_repo.get_player(CHAT, USER)).size == 100
    assert await repo.get_deposit(CHAT, USER) is None
    assert (await repo.get_corp(CHAT)).balance == 0
    factory = get_session_factory()
    async with factory() as session:
        count = await session.scalar(select(func.count(CorporationLedger.id)))
    assert count == 0


async def test_sqlite_runtime_safety_pragmas_are_enabled(db):
    from sqlalchemy import text

    from db.engine import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        foreign_keys = await session.scalar(text("PRAGMA foreign_keys"))
        busy_timeout = await session.scalar(text("PRAGMA busy_timeout"))
        journal_mode = await session.scalar(text("PRAGMA journal_mode"))
    assert foreign_keys == 1
    assert busy_timeout == 5000
    assert str(journal_mode).lower() == "wal"


async def test_open_deposit_no_size(db):
    from services import bank

    await _seed_player(0)
    with pytest.raises(bank.BankError) as e:
        await bank.open_deposit(CHAT, USER, 10)
    assert e.value.code == "no_size"


async def test_negative_dick_charge_becomes_timed_loan_without_cutting_size(db):
    from repositories import bank as repo
    from repositories import events as events_repo
    from repositories import players as players_repo
    from services import bank

    await _seed_player(50)
    due_at, defaulted = await bank.charge_dick_debt_on_dict(CHAT, USER, 7)

    player = await players_repo.get_player(CHAT, USER)
    loan = await repo.get_loan(CHAT, USER)
    assert player.size == 50
    assert (loan.principal, loan.roll_debt_principal) == (7, 7)
    assert loan.due_at == due_at
    assert defaulted is False
    events = await events_repo.get_events(CHAT, USER, types=[events_repo.DICK_DEBT])
    assert events[0].meta["amount"] == 7


async def test_pisyago_covers_poor_players_and_caps_the_period_allowance(db):
    from repositories import events as events_repo
    from repositories import players as players_repo
    from services import bank

    await _seed_player(0)
    first = await bank.apply_pisyago_on_dict(CHAT, USER, 10)
    assert (first.assets, first.coverage_pct, first.covered, first.debt) == (0, 100, 10, 0)
    assert first.remaining == 10

    await players_repo.set_player_fields(CHAT, USER, size=8)
    second = await bank.apply_pisyago_on_dict(CHAT, USER, 10)
    assert (second.assets, second.coverage_pct, second.covered, second.debt) == (8, 75, 7, 3)
    assert second.remaining == 3

    third = await bank.apply_pisyago_on_dict(CHAT, USER, 10)
    assert (third.covered, third.debt, third.remaining) == (3, 7, 0)
    player = await players_repo.get_player(CHAT, USER)
    assert player.insurance_used == 20
    assert player.insurance_reset_at == first.reset_at
    events = await events_repo.get_events(CHAT, USER, types=[events_repo.PISYAGO])
    assert [event.meta["covered"] for event in events] == [10, 7, 3]


async def test_pisyago_counts_deposits_and_renews_an_expired_allowance(db):
    import time

    from repositories import players as players_repo
    from services import bank

    await _seed_player(20)
    await bank.open_deposit(CHAT, USER, 20)
    hidden = await bank.apply_pisyago_on_dict(CHAT, USER, 10)
    assert hidden.assets == 20
    assert (hidden.coverage_pct, hidden.covered, hidden.debt) == (0, 0, 10)

    await bank.withdraw_deposit(CHAT, USER, None)
    await players_repo.set_player_fields(
        CHAT,
        USER,
        size=0,
        insurance_used=20,
        insurance_reset_at=int(time.time()) - 1,
    )
    renewed = await bank.apply_pisyago_on_dict(CHAT, USER, 10)
    assert (renewed.covered, renewed.debt, renewed.remaining) == (10, 0, 10)
    assert renewed.reset_at > int(time.time())


async def test_pisyago_counts_real_money_poker_escrow(db):
    from services import bank, poker

    await _seed_player(20)
    table = await poker.create_table(
        mode="money",
        chat_id=CHAT,
        thread_id=None,
        host_id=USER,
        host_name="Застрахованный",
        access_mode="open",
        buy_in=20,
        small_blind=1,
        big_blind=2,
        max_seats=2,
        timeout=60,
    )
    result = await bank.apply_pisyago_on_dict(CHAT, USER, 10)
    assert result.assets == 20
    assert (result.coverage_pct, result.covered, result.debt) == (0, 0, 10)
    await poker.close(table.table_id, USER)


async def test_negative_dick_charge_joins_existing_credit_and_keeps_its_due_date(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(50)
    await repo.corp_apply(CHAT, delta=100)
    await bank.take_loan(CHAT, USER, 10)
    original = await repo.get_loan(CHAT, USER)

    due_at, _ = await bank.charge_dick_debt_on_dict(CHAT, USER, 6)
    combined = await repo.get_loan(CHAT, USER)
    assert combined.principal == 16
    assert combined.roll_debt_principal == 6
    assert due_at == original.due_at


async def test_repaying_only_measurement_debt_does_not_farm_credit_rating(db):
    from repositories import players as players_repo
    from services import bank

    await _seed_player(50)
    await bank.charge_dick_debt_on_dict(CHAT, USER, 5)
    await bank.repay_loan(CHAT, USER, None)
    player = await players_repo.get_player(CHAT, USER)
    assert player.size == 45
    assert player.loans_repaid == 0


async def test_withdraw_early_penalty(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)
    await bank.open_deposit(CHAT, USER, 100)  # matures in the future
    res = await bank.withdraw_deposit(CHAT, USER, None)

    c = cfg()
    penalty = (100 * c.dep_early_penalty_pct + 99) // 100
    assert res.amount == 100 - penalty
    assert res.extra == penalty  # no accrued interest yet

    corp = await repo.get_corp(CHAT)
    assert corp.total_penalties == penalty
    assert corp.balance == penalty


async def test_accrue_deposit_idempotent_per_day(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    await repo.corp_apply(CHAT, delta=10_000)  # the till must have cash to pay interest
    first = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert first > 0
    again = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert again == 0  # already earned today
    next_day = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-04")
    assert next_day > 0


async def test_fractional_deposit_day_is_consumed_without_payout(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(20)
    await bank.open_deposit(CHAT, USER, 20)
    assert await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03") == 0
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.active_days_count == 1
    assert dep.last_accrual_day == "2026-06-03"
    assert dep.interest_remainder_ppm > 0


async def test_legacy_default_settings_are_rebalanced_once(db):
    from sqlalchemy import text

    from db import engine as engine_mod
    from repositories import global_settings as settings_repo

    await settings_repo.upsert(
        dep_rate_pct=3,
        dep_rate_decay_pct=15,
        dep_rate_floor_pct=1,
        dep_yield_cap_pct=20,
        loan_rate_pct=5,
        loan_min=5,
        loan_term_days=5,
    )
    async with engine_mod.get_engine().begin() as conn:
        await conn.execute(text("UPDATE corporation SET bank_rebalanced_v2 = 0 WHERE id = 1"))
        await conn.execute(text("DELETE FROM alembic_version"))
    await engine_mod.dispose_engine()
    await engine_mod.init_db()

    row = await settings_repo.get_row()
    assert (
        row.dep_rate_pct,
        row.dep_rate_decay_pct,
        row.dep_rate_floor_pct,
        row.dep_yield_cap_pct,
    ) == (2, 25, 0, 8)
    assert (row.loan_rate_pct, row.loan_min, row.loan_term_days) == (2, 15, 7)

    # Once versioned, startup no longer repeats a data migration or overrides
    # deliberate admin tuning that happens to match historical defaults.
    await settings_repo.upsert(
        dep_rate_pct=3,
        dep_rate_decay_pct=15,
        dep_rate_floor_pct=1,
        dep_yield_cap_pct=20,
    )
    await engine_mod.dispose_engine()
    await engine_mod.init_db()
    row = await settings_repo.get_row()
    assert (
        row.dep_rate_pct,
        row.dep_rate_decay_pct,
        row.dep_rate_floor_pct,
        row.dep_yield_cap_pct,
    ) == (3, 15, 1, 20)


async def test_deposit_interest_capped_by_empty_corp(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)  # principal funds the till (+1000)
    await repo.corp_apply(CHAT, delta=-1000)  # but drain it dry before accrual
    # Corporation is broke → it pays nothing and the house never goes negative.
    interest = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert interest == 0
    corp = await repo.get_corp(CHAT)
    assert corp.balance == 0  # never lent itself into the red
    # The active day is NOT consumed, so the depositor can still earn later.
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.active_days_count == 0


async def test_deposit_interest_paid_from_funded_corp(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)  # principal funds the till (+1000)
    await repo.corp_apply(CHAT, delta=10_000)  # plus extra house profit
    interest = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert interest > 0
    corp = await repo.get_corp(CHAT)
    # Accrual creates a liability; cash moves only on actual withdrawal.
    assert corp.balance == 11_000
    assert corp.total_interest_paid == interest


async def test_bad_credit_rejection_locks_out_reapply(db):
    from repositories import bank as repo
    from services import bank, cooldown

    cooldown.reset(CHAT, USER, "loan_denied")
    # Two defaults zero the credit multiplier → history rejection, regardless of a
    # funded till.
    await _seed_player(1000, loans_defaulted=2)
    await repo.corp_apply(CHAT, delta=10_000)
    with pytest.raises(bank.BankError) as e:
        await bank.take_loan(CHAT, USER, 50)
    assert e.value.code == "no_credit"
    # The refusal opens a cooldown: an immediate re-apply is bounced without being
    # re-evaluated, so a debtor can't spam the till.
    with pytest.raises(bank.BankError) as e:
        await bank.take_loan(CHAT, USER, 50)
    assert e.value.code == "loan_denied"
    cooldown.reset(CHAT, USER, "loan_denied")


async def test_take_loan_needs_funded_corp(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)
    # Empty till → no lending even with a healthy credit limit.
    with pytest.raises(bank.BankError) as e:
        await bank.take_loan(CHAT, USER, 50)
    assert e.value.code == "corp_broke"

    await repo.corp_apply(CHAT, delta=10_000)  # fund the till
    res = await bank.take_loan(CHAT, USER, 1000)  # over credit limit (50% of 100) → 50
    assert res.amount == 50

    from repositories import players as players_repo

    player = await players_repo.get_player(CHAT, USER)
    assert player.size == 150  # 100 liquid + 50 borrowed
    loan = await repo.get_loan(CHAT, USER)
    assert loan.principal == 50
    corp = await repo.get_corp(CHAT)
    assert corp.balance == 10_000 - 50  # cash left the vault

    with pytest.raises(bank.BankError) as e:
        await bank.take_loan(CHAT, USER, 10)
    assert e.value.code == "loan_exists"


async def test_loan_capped_by_corp_funds(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)  # credit limit 100
    await repo.corp_apply(CHAT, delta=30)  # but the till only has 30
    res = await bank.take_loan(CHAT, USER, 100)
    assert res.amount == 30  # can't borrow more than the house holds
    corp = await repo.get_corp(CHAT)
    assert corp.balance == 0


async def test_repay_full_immediately_does_not_bump_history(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(200)  # 50% credit limit → 100
    await repo.corp_apply(CHAT, delta=100)  # fund the till exactly
    await bank.take_loan(CHAT, USER, 100)  # size now 300, till now 0
    # Add some interest so we can check it routes to the Corporation.
    await repo.upsert_loan(CHAT, USER, accrued_interest=20)

    res = await bank.repay_loan(CHAT, USER, None)
    assert res.amount == 120
    assert res.extra == 20  # interest portion

    assert await repo.get_loan(CHAT, USER) is None
    from repositories import players as players_repo

    player = await players_repo.get_player(CHAT, USER)
    assert player.size == 180  # 300 - 120
    assert player.loans_repaid == 0
    corp = await repo.get_corp(CHAT)
    assert corp.total_interest_earned == 20
    assert corp.balance == 120  # principal refilled the till (0+100) + 20 interest


async def test_qualified_three_day_loan_bumps_history_once_per_window(db, monkeypatch):
    from repositories import bank as repo
    from repositories import players as players_repo
    from services import bank

    now = 2_000_000
    monkeypatch.setattr(bank, "_now", lambda: now)
    await _seed_player(200)
    await repo.corp_apply(CHAT, delta=200)
    await bank.take_loan(CHAT, USER, 100)
    await repo.upsert_loan(CHAT, USER, opened_at=now - 3 * bank.DAY, due_at=now + bank.DAY)
    await bank.repay_loan(CHAT, USER, None)
    assert (await players_repo.get_player(CHAT, USER)).loans_repaid == 1

    await bank.take_loan(CHAT, USER, 100)
    await repo.upsert_loan(CHAT, USER, opened_at=now - 3 * bank.DAY, due_at=now + bank.DAY)
    await bank.repay_loan(CHAT, USER, None)
    assert (await players_repo.get_player(CHAT, USER)).loans_repaid == 1


async def test_shortfall_bails_in_then_pays_protected_body_with_early_penalty(db):
    from repositories import bank as repo
    from repositories import players as players_repo
    from services import bank

    await _seed_player(200)
    await bank.open_deposit(CHAT, USER, 100)
    policy = await bank.buy_sekasko(CHAT, USER, 40)
    assert (policy.amount, policy.extra) == (40, 2)
    corp = await repo.get_corp(CHAT)
    assert (corp.balance, corp.insurance_reserve) == (100, 2)
    assert (await players_repo.get_player(CHAT, USER)).size == 98

    await repo.upsert_deposit(CHAT, USER, accrued=7)
    await repo.corp_apply(CHAT, delta=-100)
    result = await bank.withdraw_deposit(CHAT, USER, None)

    assert result.bail_in is not None
    assert result.bail_in.wiped == 17  # 7 interest + 10 risky principal
    assert result.extra == 27  # early penalty is applied to surviving 90 principal
    assert result.amount == 63
    assert result.bail_in.payout == 63
    assert result.bail_in.balance == -63
    assert result.bail_in.deficit == 63
    assert await repo.get_deposit(CHAT, USER) is None
    corp = await repo.get_corp(CHAT)
    assert corp.status == "recovery"
    assert corp.total_bailin == 17
    assert corp.bankruptcy_count == 1


async def test_sekasko_summary_keeps_active_coverage_without_deposit(db, monkeypatch):
    from repositories import bank as repo
    from services import bank

    now = 2_000_000
    monkeypatch.setattr(bank, "_now", lambda: now)
    await _seed_player(1)
    await repo.add_insurance(CHAT, USER, amount=7, premium=0, expires_at=now + 2 * bank.DAY)
    await repo.add_insurance(CHAT, USER, amount=5, premium=0, expires_at=now + bank.DAY)

    without_deposit = await bank.get_summary(CHAT, USER)
    assert without_deposit.deposit is None
    assert without_deposit.sekasko.active == 12
    assert without_deposit.sekasko.total_protected == 0
    assert without_deposit.sekasko.unused == 12
    assert without_deposit.sekasko.next_expires_at == now + bank.DAY
    assert without_deposit.sekasko.next_expiring == 5
    assert without_deposit.sekasko.available == 0

    await bank.open_deposit(CHAT, USER, 1)
    with_deposit = await bank.get_summary(CHAT, USER)
    assert with_deposit.sekasko.base_protected == 1
    assert with_deposit.sekasko.sekasko_protected == 0
    assert with_deposit.sekasko.total_protected == 1
    assert with_deposit.sekasko.risky == 0
    assert with_deposit.sekasko.unused == 12


def test_sekasko_premium_and_affordability_use_same_rounding():
    from services import bank

    assert bank.sekasko_premium(1, 5) == 1
    assert bank.sekasko_premium(20, 5) == 1
    assert bank.sekasko_premium(21, 5) == 2
    assert bank._affordable_sekasko(size=1, premium_pct=5, limit=40) == 20
    assert bank._affordable_sekasko(size=0, premium_pct=5, limit=40) == 0


def test_sekasko_schedule_reports_the_first_expiring_tranche_without_retroactive_cap():
    from services import bank

    policies = [
        SimpleNamespace(amount=10, expires_at=100),
        SimpleNamespace(amount=35, expires_at=200),
        SimpleNamespace(amount=5, expires_at=300),
    ]

    assert bank._sekasko_coverage_schedule(policies) == (50, 100, 10)


@pytest.mark.parametrize(
    ("principal", "base", "sekasko", "total", "risky", "unused", "issuance"),
    [
        (30, 30, 0, 30, 0, 100, 0),
        (50, 50, 0, 50, 0, 100, 0),
        (120, 50, 70, 120, 0, 30, 70),
        (150, 50, 100, 150, 0, 0, 100),
        (180, 50, 100, 150, 30, 0, 100),
    ],
)
def test_deposit_protection_layers_base_and_sekasko_on_top(
    principal: int,
    base: int,
    sekasko: int,
    total: int,
    risky: int,
    unused: int,
    issuance: int,
) -> None:
    from services import bank

    c = replace(cfg(), sekasko_max_coverage=100)
    value = bank.deposit_protection(principal, active_sekasko=100, cfg=c)

    assert (value.base, value.sekasko, value.total, value.risky) == (
        base,
        sekasko,
        total,
        risky,
    )
    assert value.unused == unused
    assert value.issuance_limit == issuance


def test_sekasko_purchase_headroom_starts_above_base_and_subtracts_active_contracts():
    from services import bank

    c = replace(cfg(), sekasko_max_coverage=100)
    assert bank.deposit_protection(120, active_sekasko=20, cfg=c).issuance_available == 50
    assert bank.deposit_protection(30, active_sekasko=20, cfg=c).issuance_available == 0


async def test_base_only_crisis_is_audited_once_even_when_nothing_is_wiped(db):
    from sqlalchemy import func, select

    from db.engine import get_session_factory
    from db.models import CorporationLedger, Event
    from repositories import bank as repo
    from repositories import events as events_repo
    from services import bank

    await _seed_player(100)
    await bank.open_deposit(CHAT, USER, 30)
    await repo.upsert_deposit(CHAT, USER, matures_at=0)
    await repo.corp_apply(CHAT, delta=-30)

    result = await bank.withdraw_deposit(CHAT, USER, None)

    assert result.amount == 30
    assert result.bail_in is not None
    assert (result.bail_in.wiped, result.bail_in.balance, result.bail_in.deficit) == (
        0,
        -30,
        30,
    )
    corp = await repo.get_corp(CHAT)
    assert (corp.bankruptcy_count, corp.total_bailin, corp.status) == (1, 0, "recovery")

    factory = get_session_factory()
    async with factory() as session:
        ledgers = list(
            (
                await session.execute(
                    select(CorporationLedger).where(
                        CorporationLedger.chat_id == CHAT,
                        CorporationLedger.reason == "bailin",
                    )
                )
            ).scalars()
        )
        event_count = await session.scalar(
            select(func.count(Event.id)).where(
                Event.chat_id == CHAT,
                Event.type == events_repo.CORP_BAILIN,
            )
        )
        sanation_count = await session.scalar(
            select(func.count(Event.id)).where(
                Event.chat_id == CHAT,
                Event.type == events_repo.CORP_SANATION,
            )
        )
    assert len(ledgers) == 1
    assert ledgers[0].meta["payout"] == 30
    assert ledgers[0].meta["balance"] == -30
    assert event_count == 1
    assert sanation_count == 0


async def test_bail_in_cuts_all_depositors_and_keeps_raw_contracts_above_current_cap(db):
    from repositories import bank as repo
    from repositories import players as players_repo
    from services import bank

    other = USER + 1
    await _seed_player(0)
    await players_repo.set_player_fields(CHAT, other, size=0)
    await repo.upsert_deposit(CHAT, USER, principal=180, accrued=5)
    await repo.upsert_deposit(CHAT, other, principal=120, accrued=7)
    await repo.add_insurance(
        CHAT,
        USER,
        amount=120,
        premium=0,
        expires_at=bank._now() + bank.DAY,
    )
    result = await bank._bail_in(CHAT, bank._now())

    first = await repo.get_deposit(CHAT, USER)
    second = await repo.get_deposit(CHAT, other)
    assert (first.principal, first.accrued) == (170, 0)
    assert (second.principal, second.accrued) == (50, 0)
    assert result.protected_claims == 220
    assert result.wiped == 92  # (10 + 5) + (70 + 7)


async def test_mature_shortfall_wipes_interest_and_clamps_request_to_surviving_body(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(300)
    await bank.open_deposit(CHAT, USER, 180)
    await repo.upsert_deposit(CHAT, USER, accrued=20, matures_at=0)
    await repo.corp_apply(CHAT, delta=-180)

    result = await bank.withdraw_deposit(CHAT, USER, 100)

    assert result.bail_in is not None
    assert result.bail_in.wiped == 150  # 20 accrued + 130 risky body
    assert result.amount == 50
    assert result.extra == 0
    assert result.bail_in.payout == 50
    assert await repo.get_deposit(CHAT, USER) is None


async def test_recovery_pays_stored_claim_but_keeps_new_risk_ops_frozen(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(500)
    await repo.upsert_deposit(CHAT, USER, principal=150, accrued=0, matures_at=0)
    await repo.corp_apply(CHAT, delta=-10)
    await repo.set_corp_fields(CHAT, status="recovery")

    for operation in (
        lambda: bank.open_deposit(CHAT, USER, 1),
        lambda: bank.buy_sekasko(CHAT, USER, 1),
        lambda: bank.take_loan(CHAT, USER, 1),
    ):
        with pytest.raises(bank.BankError) as error:
            await operation()
        assert error.value.code == "corp_frozen"

    result = await bank.withdraw_deposit(CHAT, USER, None)

    assert result.amount == 150
    assert result.bail_in is None
    corp = await repo.get_corp(CHAT)
    assert (corp.balance, corp.status, corp.bankruptcy_count) == (-160, "recovery", 0)


async def test_bail_in_failure_rolls_back_claims_ledger_counter_and_event(db, monkeypatch):
    from sqlalchemy import func, select

    from db.engine import get_session_factory
    from db.models import CorporationLedger, Event
    from repositories import bank as repo
    from repositories import events as events_repo
    from services import bank

    await _seed_player(0)
    await repo.upsert_deposit(CHAT, USER, principal=180, accrued=5)
    corp_before = await repo.get_corp(CHAT)
    factory = get_session_factory()
    async with factory() as session:
        ledger_before = await session.scalar(select(func.count(CorporationLedger.id)))
        event_before = await session.scalar(select(func.count(Event.id)))

    original = events_repo.add_event

    def explode(session, chat_id, user_id, etype, **kwargs):
        if etype == events_repo.CORP_BAILIN:
            raise RuntimeError("injected bail-in event failure")
        return original(session, chat_id, user_id, etype, **kwargs)

    monkeypatch.setattr(events_repo, "add_event", explode)
    with pytest.raises(RuntimeError, match="injected bail-in"):
        await bank._bail_in(CHAT, bank._now())

    dep = await repo.get_deposit(CHAT, USER)
    corp_after = await repo.get_corp(CHAT)
    assert (dep.principal, dep.accrued) == (180, 5)
    assert corp_after.bankruptcy_count == corp_before.bankruptcy_count
    assert corp_after.total_bailin == corp_before.total_bailin
    async with factory() as session:
        assert await session.scalar(select(func.count(CorporationLedger.id))) == ledger_before
        assert await session.scalar(select(func.count(Event.id))) == event_before


async def test_collector_immediately_resolves_legacy_sanation_and_posts_neutral_notice(db):
    from unittest.mock import AsyncMock

    from repositories import bank as repo
    from services import bank

    await _seed_player(0)
    await repo.upsert_deposit(CHAT, USER, principal=120, accrued=7)
    await repo.set_corp_fields(
        CHAT,
        status="sanation",
        sanation_started_at=1,
        sanation_deadline=9_999_999,
    )
    bot = AsyncMock()

    await bank._run_corporation_crises(bot, bank._now())

    dep = await repo.get_deposit(CHAT, USER)
    corp = await repo.get_corp(CHAT)
    assert (dep.principal, dep.accrued) == (50, 0)
    assert (corp.status, corp.bankruptcy_count, corp.total_bailin) == (
        "recovery",
        1,
        77,
    )
    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.await_args
    assert args[0] == CHAT
    assert "Старая санация завершена автоматически" in args[1]
    assert "инициатор" not in args[1].lower()
    assert kwargs["parse_mode"] == "HTML"


async def test_collector_restores_recovery_only_after_cash_and_claims_are_funded(db):
    from unittest.mock import AsyncMock

    from repositories import bank as repo
    from services import bank

    await _seed_player(0)
    await repo.upsert_deposit(CHAT, USER, principal=50, accrued=0)
    await repo.corp_apply(CHAT, delta=50)
    await repo.set_corp_fields(CHAT, status="recovery")
    bot = AsyncMock()

    await bank._run_corporation_crises(bot, bank._now())

    corp = await repo.get_corp(CHAT)
    assert corp.status == "healthy"
    bot.send_message.assert_not_awaited()


async def test_positive_dick_uses_emission_then_only_local_cash(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1)
    await repo.corp_apply(CHAT, delta=10)
    result = await bank.fund_positive_dick(CHAT, USER, 7)
    assert (result.emitted, result.corporation_paid, result.credited, result.clipped) == (
        2,
        5,
        7,
        0,
    )
    assert (await repo.get_corp(CHAT)).balance == 5

    other = CHAT - 1
    clipped = await bank.fund_positive_dick(other, USER, 7)
    assert (clipped.emitted, clipped.corporation_paid, clipped.clipped) == (2, 0, 5)
    assert (await repo.get_corp(other)).balance == 0


async def test_local_corporation_migration_splits_legacy_till_by_deposits(db):
    from sqlalchemy import text

    from db import engine as engine_mod
    from repositories import bank as repo
    from repositories import players as players_repo

    other = CHAT - 1
    await players_repo.set_player_fields(CHAT, USER, size=1)
    await players_repo.set_player_fields(other, USER, size=1)
    await repo.upsert_deposit(CHAT, USER, principal=100)
    await repo.upsert_deposit(other, USER, principal=300)
    async with engine_mod.get_engine().begin() as conn:
        await conn.execute(text("UPDATE corporation SET balance = 100 WHERE id = 1"))
        await conn.execute(text("DELETE FROM chat_corporations"))
        await conn.execute(
            text("UPDATE alembic_version SET version_num = '0003_pisyago_insurance'")
        )
    await engine_mod.dispose_engine()
    await engine_mod.init_db()

    assert (await repo.get_corp(CHAT)).balance == 25
    assert (await repo.get_corp(other)).balance == 75
    async with engine_mod.get_engine().connect() as conn:
        assert await conn.scalar(text("SELECT balance FROM corporation WHERE id = 1")) == 0


async def test_garnish_only_when_defaulted(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)
    await repo.corp_apply(CHAT, delta=100)  # fund the till
    await bank.take_loan(CHAT, USER, 100)  # not defaulted yet

    pdict = {"size": 100}
    assert await bank.garnish_on_dict(CHAT, USER, pdict, 50) == 0
    assert pdict["size"] == 100  # untouched

    await repo.upsert_loan(CHAT, USER, defaulted=True)
    taken = await bank.garnish_on_dict(CHAT, USER, pdict, 50)
    c = cfg()
    expected = (50 * c.loan_garnish_pct + 99) // 100
    assert taken == expected
    assert pdict["size"] == 100 - expected


async def test_garnish_clearing_default_does_not_credit_history(db):
    from repositories import bank as repo
    from repositories import players as players_repo
    from services import bank

    await _seed_player(1000)
    await repo.corp_apply(CHAT, delta=100)
    await bank.take_loan(CHAT, USER, 100)
    await repo.upsert_loan(CHAT, USER, defaulted=True)

    # A large gain garnishes enough to wipe the whole debt in one pass.
    pdict = {"size": 1000}
    taken = await bank.garnish_on_dict(CHAT, USER, pdict, 1000)
    assert taken == 100  # full principal recovered
    assert await repo.get_loan(CHAT, USER) is None  # debt cleared

    player = await players_repo.get_player(CHAT, USER)
    # Forced recovery on a default must NOT count as a clean repayment.
    assert player.loans_repaid == 0


async def test_accrue_loan_interest_frozen_on_default(db):
    from repositories import bank as repo
    from services import bank

    c = cfg()
    now = 1_000_000
    await repo.upsert_loan(
        CHAT,
        USER,
        principal=100,
        accrued_interest=0,
        last_accrual_at=now - 10 * bank.DAY,
        defaulted=True,
    )
    loan = await repo.get_loan(CHAT, USER)
    # A defaulted debt is frozen: no further interest accrues.
    grew = await bank.accrue_loan_interest(loan, c, now)
    assert grew == 0
    after = await repo.get_loan(CHAT, USER)
    assert after.accrued_interest == 0


async def test_recover_from_deposit_pays_debt_from_principal(db):
    from repositories import bank as repo
    from repositories import players as players_repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 400)  # principal funds the till; size now 600
    await bank.take_loan(CHAT, USER, 100)  # 50% of 600 credit limit covers 100
    await repo.upsert_loan(CHAT, USER, accrued_interest=20, defaulted=True)

    corp_before = (await repo.get_corp(CHAT)).balance
    loan = await repo.get_loan(CHAT, USER)
    recovered = await bank.recover_from_deposit(loan)
    assert recovered == 120  # full debt (100 principal + 20 interest)

    # Debt cleared, but forced recovery does NOT credit credit history.
    assert await repo.get_loan(CHAT, USER) is None
    player = await players_repo.get_player(CHAT, USER)
    assert player.loans_repaid == 0

    dep = await repo.get_deposit(CHAT, USER)
    assert dep.principal == 400 - 120  # pulled out of the deposit body

    corp = await repo.get_corp(CHAT)
    # Cash already sat in the till — no movement, only the interest slice booked.
    assert corp.balance == corp_before
    assert corp.total_interest_earned == 20


async def test_recover_from_deposit_noop_without_default(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    await repo.corp_apply(CHAT, delta=100)
    await bank.take_loan(CHAT, USER, 100)  # not defaulted

    loan = await repo.get_loan(CHAT, USER)
    assert await bank.recover_from_deposit(loan) == 0
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.principal == 1000  # untouched


async def test_overdue_loan_recovery_ignores_base_deposit_protection(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(0)
    await repo.upsert_deposit(CHAT, USER, principal=50, accrued=0)
    await repo.upsert_loan(
        CHAT,
        USER,
        principal=20,
        accrued_interest=0,
        defaulted=True,
    )

    recovered = await bank.recover_from_deposit(await repo.get_loan(CHAT, USER))

    assert recovered == 20
    assert (await repo.get_deposit(CHAT, USER)).principal == 30


async def test_confiscation_once_per_day(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    dep = await repo.get_deposit(CHAT, USER)

    class _Rng:
        def random(self):
            return 0.0  # always below chance → fire

        def uniform(self, a, b):
            return b

    seized = await bank.roll_confiscation(dep, cfg(), "2026-06-05", rng=_Rng())
    assert seized > 0
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.last_confisc_day == "2026-06-05"

    # A second roll the same day is a no-op regardless of the rng.
    again = await bank.roll_confiscation(dep, cfg(), "2026-06-05", rng=_Rng())
    assert again == 0
    # ...but the next day can fire again.
    next_day = await bank.roll_confiscation(dep, cfg(), "2026-06-06", rng=_Rng())
    assert next_day > 0


async def test_confiscation_miss_still_marks_day(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    dep = await repo.get_deposit(CHAT, USER)

    class _NeverRng:
        def random(self):
            return 1.0  # above chance → miss

        def uniform(self, a, b):
            return b

    seized = await bank.roll_confiscation(dep, cfg(), "2026-06-05", rng=_NeverRng())
    assert seized == 0
    dep = await repo.get_deposit(CHAT, USER)
    # A missed roll still consumes the day, so the collector won't retry within it.
    assert dep.last_confisc_day == "2026-06-05"


async def test_roll_confiscation_deterministic(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    dep = await repo.get_deposit(CHAT, USER)

    # rng forced to always fire and seize the max fraction.
    class _Rng:
        def random(self):
            return 0.0  # below chance → confiscate

        def uniform(self, a, b):
            return b  # max fraction

    seized = await bank.roll_confiscation(dep, cfg(), rng=_Rng())
    assert seized > 0
    corp = await repo.get_corp(CHAT)
    assert corp.total_penalties == seized
    # The 1000 principal already funded the till on open; confiscation only books
    # the seized slice as earnings without moving cash, so the balance is unchanged.
    assert corp.balance == 1000


async def test_confiscation_respects_base_and_sekasko_boundaries(db):
    from repositories import bank as repo
    from repositories import players as players_repo
    from services import bank

    class _Rng:
        def random(self):
            return 0.0

        def uniform(self, a, b):
            return b

    cases = [
        (USER, 30, 0, 0),
        (USER + 1, 50, 0, 0),
        (USER + 2, 120, 0, 7),
        (USER + 3, 150, 100, 0),
        (USER + 4, 180, 100, 3),
    ]
    for user_id, principal, coverage, expected in cases:
        await players_repo.set_player_fields(CHAT, user_id, size=0)
        await repo.upsert_deposit(CHAT, user_id, principal=principal, accrued=0)
        if coverage:
            await repo.add_insurance(
                CHAT,
                user_id,
                amount=coverage,
                premium=0,
                expires_at=bank._now() + bank.DAY,
            )
        dep = await repo.get_deposit(CHAT, user_id)

        seized = await bank.roll_confiscation(dep, cfg(), rng=_Rng())

        assert seized == expected
        assert (await repo.get_deposit(CHAT, user_id)).principal == principal - expected


async def test_confiscation_failure_rolls_back_claim_ledger_counter_and_event(db, monkeypatch):
    from sqlalchemy import func, select

    from db.engine import get_session_factory
    from db.models import CorporationLedger, Event
    from repositories import bank as repo
    from repositories import events as events_repo
    from services import bank

    class _Rng:
        def random(self):
            return 0.0

        def uniform(self, a, b):
            return b

    await _seed_player(0)
    await repo.upsert_deposit(CHAT, USER, principal=120, accrued=0)
    corp_before = await repo.get_corp(CHAT)
    factory = get_session_factory()
    async with factory() as session:
        ledger_before = await session.scalar(select(func.count(CorporationLedger.id)))
        event_before = await session.scalar(select(func.count(Event.id)))

    original = events_repo.add_event

    def explode(session, chat_id, user_id, etype, **kwargs):
        if etype == events_repo.CONFISCATION:
            raise RuntimeError("injected confiscation event failure")
        return original(session, chat_id, user_id, etype, **kwargs)

    monkeypatch.setattr(events_repo, "add_event", explode)
    dep = await repo.get_deposit(CHAT, USER)
    with pytest.raises(RuntimeError, match="injected confiscation"):
        await bank.roll_confiscation(dep, cfg(), rng=_Rng())

    assert (await repo.get_deposit(CHAT, USER)).principal == 120
    corp_after = await repo.get_corp(CHAT)
    assert corp_after.total_penalties == corp_before.total_penalties
    async with factory() as session:
        assert await session.scalar(select(func.count(CorporationLedger.id))) == ledger_before
        assert await session.scalar(select(func.count(Event.id))) == event_before
