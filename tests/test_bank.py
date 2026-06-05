"""Unit tests for the deposit/loan/Corporation economy (services/bank.py).

Pure helpers are tested directly; the async ops run against a throwaway SQLite
file (one per test) so money conservation and the Corporation balance can be
asserted end-to-end. The global config is left at its defaults (the cache is
empty, so ``get_config_sync`` returns ``_defaults()``).
"""
from __future__ import annotations

import os
import random
import tempfile

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


def test_deposit_day_interest_floor_for_small_principal():
    from services import bank

    c = cfg()
    # A tiny deposit truncates raw interest to 0 (10 * 3% = 0.3 -> 0) but the floor
    # pays 1 as long as the yield cap leaves headroom.
    assert int(10 * c.dep_rate_pct / 100) == 0  # would truncate without the floor
    assert bank.deposit_day_interest(10, 0, 0, c) == 1
    # ...but the floor never breaches the cap: at the cap there is no headroom.
    cap_total = 10 * c.dep_yield_cap_pct // 100
    assert bank.deposit_day_interest(10, cap_total, 0, c) == 0


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


async def test_open_deposit_no_size(db):
    from services import bank

    await _seed_player(0)
    with pytest.raises(bank.BankError) as e:
        await bank.open_deposit(CHAT, USER, 10)
    assert e.value.code == "no_size"


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

    corp = await repo.get_corp()
    assert corp.total_penalties == penalty
    assert corp.balance == penalty


async def test_accrue_deposit_idempotent_per_day(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    await repo.corp_apply(delta=10_000)  # the till must have cash to pay interest
    first = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert first > 0
    again = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert again == 0  # already earned today
    next_day = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-04")
    assert next_day > 0


async def test_deposit_interest_capped_by_empty_corp(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)  # principal funds the till (+1000)
    await repo.corp_apply(delta=-1000)  # but drain it dry before accrual
    # Corporation is broke → it pays nothing and the house never goes negative.
    interest = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert interest == 0
    corp = await repo.get_corp()
    assert corp.balance == 0  # never lent itself into the red
    # The active day is NOT consumed, so the depositor can still earn later.
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.active_days_count == 0


async def test_deposit_interest_paid_from_funded_corp(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)  # principal funds the till (+1000)
    await repo.corp_apply(delta=10_000)  # plus extra house profit
    interest = await bank.accrue_deposit_on_play(CHAT, USER, "2026-06-03")
    assert interest > 0
    corp = await repo.get_corp()
    assert corp.balance == 11_000 - interest
    assert corp.total_interest_paid == interest


async def test_bad_credit_rejection_locks_out_reapply(db):
    from repositories import bank as repo
    from services import bank, cooldown

    cooldown.reset(CHAT, USER, "loan_denied")
    # Two defaults zero the credit multiplier → history rejection, regardless of a
    # funded till.
    await _seed_player(1000, loans_defaulted=2)
    await repo.corp_apply(delta=10_000)
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

    await repo.corp_apply(delta=10_000)  # fund the till
    res = await bank.take_loan(CHAT, USER, 1000)  # over credit limit (50% of 100) → 50
    assert res.amount == 50

    from repositories import players as players_repo

    player = await players_repo.get_player(CHAT, USER)
    assert player.size == 150  # 100 liquid + 50 borrowed
    loan = await repo.get_loan(CHAT, USER)
    assert loan.principal == 50
    corp = await repo.get_corp()
    assert corp.balance == 10_000 - 50  # cash left the vault

    with pytest.raises(bank.BankError) as e:
        await bank.take_loan(CHAT, USER, 10)
    assert e.value.code == "loan_exists"


async def test_loan_capped_by_corp_funds(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)  # credit limit 100
    await repo.corp_apply(delta=30)  # but the till only has 30
    res = await bank.take_loan(CHAT, USER, 100)
    assert res.amount == 30  # can't borrow more than the house holds
    corp = await repo.get_corp()
    assert corp.balance == 0


async def test_repay_full_clears_and_bumps_history(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(200)  # 50% credit limit → 100
    await repo.corp_apply(delta=100)  # fund the till exactly
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
    assert player.loans_repaid == 1
    corp = await repo.get_corp()
    assert corp.total_interest_earned == 20
    assert corp.balance == 120  # principal refilled the till (0+100) + 20 interest


async def test_garnish_only_when_defaulted(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(100)
    await repo.corp_apply(delta=100)  # fund the till
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
    from services import bank
    from repositories import players as players_repo

    await _seed_player(1000)
    await repo.corp_apply(delta=100)
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
        CHAT, USER, principal=100, accrued_interest=0,
        last_accrual_at=now - 10 * bank.DAY, defaulted=True,
    )
    loan = await repo.get_loan(CHAT, USER)
    # A defaulted debt is frozen: no further interest accrues.
    grew = await bank.accrue_loan_interest(loan, c, now)
    assert grew == 0
    after = await repo.get_loan(CHAT, USER)
    assert after.accrued_interest == 0


async def test_recover_from_deposit_pays_debt_from_principal(db):
    from repositories import bank as repo
    from services import bank
    from repositories import players as players_repo

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 400)  # principal funds the till; size now 600
    await bank.take_loan(CHAT, USER, 100)  # 50% of 600 credit limit covers 100
    await repo.upsert_loan(CHAT, USER, accrued_interest=20, defaulted=True)

    corp_before = (await repo.get_corp()).balance
    loan = await repo.get_loan(CHAT, USER)
    recovered = await bank.recover_from_deposit(loan)
    assert recovered == 120  # full debt (100 principal + 20 interest)

    # Debt cleared, but forced recovery does NOT credit credit history.
    assert await repo.get_loan(CHAT, USER) is None
    player = await players_repo.get_player(CHAT, USER)
    assert player.loans_repaid == 0

    dep = await repo.get_deposit(CHAT, USER)
    assert dep.principal == 400 - 120  # pulled out of the deposit body

    corp = await repo.get_corp()
    # Cash already sat in the till — no movement, only the interest slice booked.
    assert corp.balance == corp_before
    assert corp.total_interest_earned == 20


async def test_recover_from_deposit_noop_without_default(db):
    from repositories import bank as repo
    from services import bank

    await _seed_player(1000)
    await bank.open_deposit(CHAT, USER, 1000)
    await repo.corp_apply(delta=100)
    await bank.take_loan(CHAT, USER, 100)  # not defaulted

    loan = await repo.get_loan(CHAT, USER)
    assert await bank.recover_from_deposit(loan) == 0
    dep = await repo.get_deposit(CHAT, USER)
    assert dep.principal == 1000  # untouched


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
    corp = await repo.get_corp()
    assert corp.total_penalties == seized
    # The 1000 principal already funded the till on open; confiscation only books
    # the seized slice as earnings without moving cash, so the balance is unchanged.
    assert corp.balance == 1000
