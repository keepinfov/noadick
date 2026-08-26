from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    from db import engine as engine_mod
    from services import global_settings, settings

    await engine_mod.dispose_engine()
    settings._cache.clear()
    global_settings.invalidate()
    await engine_mod.init_db()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        settings._cache.clear()
        global_settings.invalidate()
        os.unlink(path)


CHAT = -9001
USER = 91


async def _seed(size: int = 100, **fields):
    from repositories import players

    return await players.set_player_fields(CHAT, USER, size=size, **fields)


async def test_all_telegram_values_decode_and_match_seeded_payout_table(db) -> None:
    from services import casino

    rules = {rule.slot_value: rule for rule in await casino.list_payout_rules()}
    counts: Counter[int] = Counter()
    for value in range(1, 65):
        symbols = casino.decode_slot(value)
        assert len(symbols) == 3
        assert set(symbols) <= set(casino.SLOT_SYMBOLS)
        assert casino.encode_slot(symbols) == value
        counts[rules[value].payout_value if value in rules else 0] += 1

    assert casino.decode_slot(64) == (casino.SEVEN,) * 3
    assert (rules[64].payout_kind, rules[64].payout_value) == ("multiplier", 18)
    assert len(rules) == 13
    assert counts == {0: 51, 3: 9, 5: 3, 18: 1}
    assert sum(rule.payout_value for rule in rules.values()) == 60


@pytest.mark.parametrize("value", [0, 65, -1, True, 1.5, "64"])
def test_invalid_telegram_values_are_rejected(value) -> None:
    from services import casino

    with pytest.raises(casino.CasinoError) as error:
        casino.decode_slot(value)
    assert error.value.code == "bad_outcome"


@pytest.mark.parametrize(
    "symbols",
    [(), ("bar",), ("bar", "bar"), ("bar", "bar", "bar", "bar"), ("bar", "wat", "bar")],
)
def test_invalid_symbol_combinations_are_rejected(symbols) -> None:
    from services import casino

    with pytest.raises(casino.CasinoError) as error:
        casino.encode_slot(symbols)
    assert error.value.code == "bad_outcome"


async def test_payout_rules_create_move_reject_duplicates_and_delete(db) -> None:
    from services import casino

    with pytest.raises(casino.CasinoError) as error:
        await casino.save_payout_rule(
            original_slot_value=None,
            slot_value=64,
            payout_kind="fixed",
            payout_value=100,
        )
    assert error.value.code == "duplicate_payout_rule"

    moved = await casino.save_payout_rule(
        original_slot_value=64,
        slot_value=2,
        payout_kind="fixed",
        payout_value=100,
    )
    assert (moved.slot_value, moved.payout_kind, moved.payout_value) == (2, "fixed", 100)
    assert await casino.get_payout_rule(64) is None
    assert (await casino.get_payout_rule(2)).payout_value == 100
    assert await casino.delete_payout_rule(2) is True
    assert await casino.delete_payout_rule(2) is False


@pytest.mark.parametrize(
    ("slot_value", "kind", "value", "code"),
    [
        (0, "fixed", 10, "bad_outcome"),
        (65, "fixed", 10, "bad_outcome"),
        (2, "bonus", 10, "bad_payout_kind"),
        (2, "multiplier", 101, "bad_payout_value"),
        (2, "fixed", 5001, "bad_payout_value"),
        (2, "fixed", True, "bad_payout_value"),
    ],
)
async def test_payout_rule_validation(db, slot_value, kind, value, code) -> None:
    from services import casino

    with pytest.raises(casino.CasinoError) as error:
        await casino.save_payout_rule(
            original_slot_value=None,
            slot_value=slot_value,
            payout_kind=kind,
            payout_value=value,
        )
    assert error.value.code == code


async def test_saved_stake_defaults_persists_and_does_not_spin(db) -> None:
    from repositories import events, players
    from services import casino

    assert await casino.get_stake(CHAT, USER) == 5
    assert await casino.save_stake(CHAT, USER, 17) == 17
    player = await players.get_player(CHAT, USER)
    assert (player.casino_stake, player.size, player.last_casino_spin_at) == (17, 0, 0)
    assert await events.get_events(CHAT, USER) == []


@pytest.mark.parametrize("stake", [0, 51, -5, True])
async def test_stake_bounds_are_strict_and_do_not_create_player(db, stake) -> None:
    from repositories import players
    from services import casino

    with pytest.raises(casino.CasinoError) as error:
        await casino.save_stake(CHAT, USER, stake)
    assert error.value.code == "bad_stake"
    assert await players.get_player(CHAT, USER) is None


async def test_local_ban_rejects_save_and_spin_without_changing_stake(db) -> None:
    from repositories import players
    from services import casino

    await _seed(size=100, casino_stake=7, is_chat_banned=True)
    with pytest.raises(casino.CasinoError) as error:
        await casino.save_stake(CHAT, USER, 20)
    assert error.value.code == "locally_banned"
    assert (await players.get_player(CHAT, USER)).casino_stake == 7

    calls = 0

    async def outcome():
        nonlocal calls
        calls += 1
        return 2

    with pytest.raises(casino.CasinoError) as error:
        await casino.play(CHAT, USER, outcome, now=1_800_000_000)
    assert error.value.code == "locally_banned"
    assert calls == 0
    player = await players.get_player(CHAT, USER)
    assert (player.size, player.casino_stake, player.last_casino_spin_at) == (100, 7, 0)


async def test_loss_moves_stake_to_corporation_and_records_one_event(db) -> None:
    from db.engine import get_session_factory
    from db.models import CorporationLedger, Event
    from repositories import bank, events, players
    from services import casino

    await _seed(size=100, casino_stake=10)

    async def outcome():
        return casino.SlotOutcome(2, message_id=777)

    result = await casino.play(CHAT, USER, outcome, now=1_800_000_000)

    assert (result.stake, result.multiplier, result.gross_payout, result.net) == (10, 0, 0, -10)
    assert (result.size_after, result.corporation_balance, result.deficit) == (90, 10, 0)
    assert result.message_id == 777
    player = await players.get_player(CHAT, USER)
    assert (player.size, player.casino_stake, player.last_casino_spin_at) == (
        90,
        10,
        1_800_000_000,
    )
    assert (await bank.get_corp(CHAT)).balance == 10
    factory = get_session_factory()
    async with factory() as session:
        ledgers = list((await session.execute(select(CorporationLedger))).scalars())
        recorded = list((await session.execute(select(Event))).scalars())
    assert [(row.reason, row.cash_delta) for row in ledgers] == [("casino_spin", 10)]
    assert [(row.type, row.delta) for row in recorded] == [(events.CASINO_SPIN, -10)]
    assert recorded[0].meta["message_id"] == 777
    assert recorded[0].meta["symbols"] == list(result.symbols)


async def test_loss_never_bails_in_an_underreserved_healthy_corporation(db) -> None:
    from db.engine import get_session_factory
    from db.models import Deposit
    from repositories import bank, players
    from services import casino

    await _seed(size=100, casino_stake=10)
    await players.set_player_fields(CHAT, 92, size=0)
    await bank.upsert_deposit(CHAT, 92, principal=200, accrued=10)

    async def outcome():
        return 2

    result = await casino.play(CHAT, USER, outcome, now=1_800_000_050)
    assert result.net == -10
    assert result.bail_in is None
    corp = await bank.get_corp(CHAT)
    assert (corp.balance, corp.status, corp.bankruptcy_count, corp.total_bailin) == (
        10,
        "healthy",
        0,
        0,
    )
    factory = get_session_factory()
    async with factory() as session:
        deposit = await session.get(Deposit, (CHAT, 92))
    assert (deposit.principal, deposit.accrued) == (200, 10)


async def test_fixed_gross_payout_can_be_smaller_than_stake_and_is_recorded(db) -> None:
    from db.engine import get_session_factory
    from db.models import Event
    from repositories import players
    from services import casino

    await _seed(size=100, casino_stake=10)
    await casino.save_payout_rule(
        original_slot_value=None,
        slot_value=2,
        payout_kind="fixed",
        payout_value=7,
    )

    async def outcome():
        return casino.SlotOutcome(2, message_id=778)

    result = await casino.play(CHAT, USER, outcome, now=1_800_000_075)
    assert (result.payout_kind, result.payout_value, result.multiplier) == ("fixed", 7, 0)
    assert (result.gross_payout, result.net, result.size_after) == (7, -3, 97)
    assert (await players.get_player(CHAT, USER)).size == 97
    factory = get_session_factory()
    async with factory() as session:
        event = (
            await session.execute(select(Event).where(Event.type == "casino_spin"))
        ).scalar_one()
    assert event.meta["payout_kind"] == "fixed"
    assert event.meta["payout_value"] == 7
    assert event.meta["multiplier"] == 0


async def test_one_off_stake_wins_without_changing_saved_default_or_garnishing(db) -> None:
    from db.engine import get_session_factory
    from db.models import Loan
    from repositories import bank, players
    from services import casino

    now = 1_800_000_100
    await _seed(size=100, casino_stake=7)
    await bank.corp_apply(CHAT, delta=1_000)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        session.add(
            Loan(
                chat_id=CHAT,
                user_id=USER,
                principal=50,
                accrued_interest=10,
                opened_at=now - 100,
                due_at=now - 1,
                defaulted=True,
            )
        )

    async def outcome():
        return 1

    result = await casino.play(CHAT, USER, outcome, stake=10, now=now)
    assert (result.multiplier, result.gross_payout, result.net) == (5, 50, 40)
    player = await players.get_player(CHAT, USER)
    assert (player.size, player.casino_stake) == (140, 7)
    async with factory() as session:
        loan = await session.get(Loan, (CHAT, USER))
    assert (loan.principal, loan.accrued_interest) == (50, 10)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("missing", "no_player"),
        ("empty", "no_size"),
        ("insufficient", "insufficient"),
        ("disabled", "disabled"),
        ("recovery", "corp_frozen"),
        ("cooldown", "cooldown"),
    ],
)
async def test_preflight_rejections_never_call_supplier(db, setup: str, expected: str) -> None:
    from repositories import bank, chat_settings, players
    from services import casino, settings

    now = 1_800_000_200
    if setup != "missing":
        size = 0 if setup == "empty" else 5 if setup == "insufficient" else 100
        await _seed(size=size, casino_stake=10)
    if setup == "disabled":
        await chat_settings.upsert_settings(CHAT, casino_enabled=False)
        settings.invalidate(CHAT)
    if setup == "recovery":
        await bank.set_corp_fields(CHAT, status="recovery")
    if setup == "cooldown":
        await players.set_player_fields(CHAT, USER, last_casino_spin_at=now - 1)

    calls = 0

    async def outcome():
        nonlocal calls
        calls += 1
        return 2

    with pytest.raises(casino.CasinoError) as error:
        await casino.play(CHAT, USER, outcome, now=now)
    assert error.value.code == expected
    assert calls == 0


async def test_supplier_failure_and_invalid_result_leave_no_mutation(db) -> None:
    from db.engine import get_session_factory
    from db.models import CorporationLedger, Event
    from repositories import bank, players
    from services import casino

    await _seed(size=100, casino_stake=10)

    async def failure():
        raise RuntimeError("telegram failed")

    with pytest.raises(RuntimeError, match="telegram failed"):
        await casino.play(CHAT, USER, failure, now=1_800_000_300)

    async def invalid():
        return 65

    with pytest.raises(casino.CasinoError) as error:
        await casino.play(CHAT, USER, invalid, now=1_800_000_300)
    assert error.value.code == "bad_outcome"
    player = await players.get_player(CHAT, USER)
    assert (player.size, player.last_casino_spin_at) == (100, 0)
    assert (await bank.get_corp(CHAT)).balance == 0
    factory = get_session_factory()
    async with factory() as session:
        assert await session.scalar(select(func.count(CorporationLedger.id))) == 0
        assert await session.scalar(select(func.count(Event.id))) == 0


async def test_concurrent_spins_are_serialized_before_second_supplier(db) -> None:
    from repositories import players
    from services import casino

    await _seed(size=100, casino_stake=10)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def outcome():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return 2

    first = asyncio.create_task(casino.play(CHAT, USER, outcome, now=1_800_000_400))
    await entered.wait()
    second = asyncio.create_task(casino.play(CHAT, USER, outcome, now=1_800_000_400))
    await asyncio.sleep(0)
    release.set()
    first_result, second_result = await asyncio.gather(first, second, return_exceptions=True)

    assert isinstance(first_result, casino.CasinoResult)
    assert isinstance(second_result, casino.CasinoError)
    assert second_result.code == "cooldown"
    assert calls == 1
    assert (await players.get_player(CHAT, USER)).size == 90


async def test_settlement_failure_rolls_back_every_leg_and_cooldown(db, monkeypatch) -> None:
    from db.engine import get_session_factory
    from db.models import CorporationLedger, Event
    from repositories import bank, events, players
    from services import casino

    await _seed(size=100, casino_stake=10)

    def explode(*_args, **_kwargs):
        raise RuntimeError("injected casino event failure")

    monkeypatch.setattr(events, "add_event", explode)

    async def outcome():
        return casino.SlotOutcome(64, 333)

    with pytest.raises(RuntimeError, match="injected casino"):
        await casino.play(CHAT, USER, outcome, now=1_800_000_500)

    player = await players.get_player(CHAT, USER)
    assert (player.size, player.last_casino_spin_at) == (100, 0)
    assert (await bank.get_corp(CHAT)).balance == 0
    factory = get_session_factory()
    async with factory() as session:
        assert await session.scalar(select(func.count(CorporationLedger.id))) == 0
        assert await session.scalar(select(func.count(Event.id))) == 0


async def test_jackpot_bails_in_globally_then_pays_in_full_once(db) -> None:
    from db.engine import get_session_factory
    from db.models import CorporationLedger, Deposit, Event
    from repositories import bank, events, players
    from services import casino

    await _seed(size=100, casino_stake=10)
    await players.set_player_fields(CHAT, 92, size=0)
    await bank.upsert_deposit(CHAT, 92, principal=200, accrued=0)

    async def jackpot():
        return casino.SlotOutcome(64, 999)

    result = await casino.play(CHAT, USER, jackpot, now=1_800_000_600)
    assert (result.gross_payout, result.net, result.size_after) == (180, 170, 270)
    assert result.bail_in is not None
    assert (result.bail_in.wiped, result.bail_in.protected_claims) == (150, 50)
    assert (result.corporation_balance, result.deficit) == (-170, 220)

    corp = await bank.get_corp(CHAT)
    assert (corp.balance, corp.status, corp.bankruptcy_count, corp.total_bailin) == (
        -170,
        "recovery",
        1,
        150,
    )
    factory = get_session_factory()
    async with factory() as session:
        deposit = await session.get(Deposit, (CHAT, 92))
        ledgers = list(
            (
                await session.execute(select(CorporationLedger).order_by(CorporationLedger.id))
            ).scalars()
        )
        recorded = list((await session.execute(select(Event).order_by(Event.id))).scalars())
    assert (deposit.principal, deposit.accrued) == (50, 0)
    assert [(row.reason, row.cash_delta) for row in ledgers] == [
        ("casino_spin", -170),
        ("bailin", 0),
    ]
    assert [row.type for row in recorded] == [events.CORP_BAILIN, events.CASINO_SPIN]
    assert ledgers[1].meta["source"] == "casino"
    assert recorded[0].meta["source"] == "casino"
    assert recorded[1].meta["message_id"] == 999


async def test_casino_delta_counts_once_in_seasons_analytics_and_metrics(db) -> None:
    from db.engine import get_session_factory
    from db.models import Event
    from services import analytics, casino, economy_metrics, seasons

    await _seed(size=100, casino_stake=10)

    async def outcome():
        return 2

    result = await casino.play(CHAT, USER, outcome, now=1_800_000_700)
    factory = get_session_factory()
    async with factory() as session:
        event = (
            await session.execute(select(Event).where(Event.type == "casino_spin"))
        ).scalar_one()
    assert seasons.competitive_wealth_delta(event) == result.net
    assert seasons._liquid_delta(event) == result.net
    assert analytics._wealth_delta(event) == result.net
    assert event.type in analytics.SIZE_EVENT_TYPES
    snapshot = await economy_metrics.snapshot(now=1_800_000_701)
    assert (snapshot.net_delta_7d, snapshot.net_delta_30d) == (result.net, result.net)


def _migration_config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.attributes["configure_logger"] = False
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def _casino_columns(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    with sqlite3.connect(path) as database:
        return (
            {
                str(row[1]): str(row[4] or "").strip("()'\" ")
                for row in database.execute("PRAGMA table_info(players)")
            },
            {
                str(row[1]): str(row[4] or "").strip("()'\" ")
                for row in database.execute("PRAGMA table_info(chat_settings)")
            },
        )


def test_0009_migration_roundtrip_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "casino-0008.db"
    config = _migration_config(path)
    command.upgrade(config, "0008_season_sekasko_prizes")
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE players DROP COLUMN last_casino_spin_at")
        database.execute("ALTER TABLE players DROP COLUMN casino_stake")
        database.execute("ALTER TABLE chat_settings DROP COLUMN casino_enabled")

    command.upgrade(config, "head")
    players, chat_settings = _casino_columns(path)
    assert players["casino_stake"] == "5"
    assert players["last_casino_spin_at"] == "0"
    assert chat_settings["casino_enabled"] == "1"

    command.downgrade(config, "0008_season_sekasko_prizes")
    players, chat_settings = _casino_columns(path)
    assert "casino_stake" not in players
    assert "last_casino_spin_at" not in players
    assert "casino_enabled" not in chat_settings

    command.upgrade(config, "head")
    players, chat_settings = _casino_columns(path)
    assert {"casino_stake", "last_casino_spin_at"} <= players.keys()
    assert "casino_enabled" in chat_settings


def test_0011_migration_seeds_current_rules_and_enforces_bounds(tmp_path: Path) -> None:
    path = tmp_path / "casino-0010.db"
    config = _migration_config(path)
    command.upgrade(config, "0010_public_profile_label")
    command.upgrade(config, "head")

    with sqlite3.connect(path) as database:
        rules = database.execute(
            "SELECT slot_value, payout_kind, payout_value "
            "FROM casino_payout_rules ORDER BY slot_value"
        ).fetchall()
        assert len(rules) == 13
        assert rules[-1] == (64, "multiplier", 18)
        assert sum(rule[2] for rule in rules) == 60
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO casino_payout_rules "
                "(slot_value, payout_kind, payout_value) VALUES (2, 'multiplier', 101)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            database.execute(
                "INSERT INTO casino_payout_rules "
                "(slot_value, payout_kind, payout_value) VALUES (65, 'fixed', 10)"
            )

    command.downgrade(config, "0010_public_profile_label")
    with sqlite3.connect(path) as database:
        assert "casino_payout_rules" not in {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
