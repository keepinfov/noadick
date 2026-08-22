from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


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


def ts(value: str, zone: str = "UTC") -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(zone)).timestamp())


async def test_migration_creates_season_constraints_and_indexes(db) -> None:
    from db.engine import get_engine

    async with get_engine().connect() as connection:
        (
            tables,
            season_indexes,
            player_indexes,
            policy_columns,
            policy_defaults,
            global_columns,
            global_defaults,
            player_defaults,
        ) = await connection.run_sync(
            lambda sync: (
                set(inspect(sync).get_table_names()),
                {value["name"] for value in inspect(sync).get_indexes("weekly_seasons")},
                {value["name"] for value in inspect(sync).get_indexes("weekly_season_players")},
                {value["name"] for value in inspect(sync).get_columns("deposit_insurance")},
                {
                    value["name"]: value["default"]
                    for value in inspect(sync).get_columns("deposit_insurance")
                },
                {value["name"] for value in inspect(sync).get_columns("global_settings")},
                {
                    value["name"]: value["default"]
                    for value in inspect(sync).get_columns("global_settings")
                },
                {
                    value["name"]: value["default"]
                    for value in inspect(sync).get_columns("weekly_season_players")
                },
            )
        )
    assert {"weekly_seasons", "weekly_season_players"} <= tables
    assert "uq_weekly_seasons_live_chat" in season_indexes
    assert "ix_weekly_season_players_dick" in player_indexes
    assert {"source", "source_season_id"} <= policy_columns
    assert {
        "dep_risk_free_principal",
        "season_sekasko_prize_places",
        "season_sekasko_prize_coverage",
        "corp_sanation_days",
    } <= global_columns
    assert str(global_defaults["dick_emission_cap"]).strip("()'\"") == "2"
    assert str(global_defaults["sekasko_max_coverage"]).strip("()'\"") == "100"
    assert str(global_defaults["dep_risk_free_principal"]).strip("()'\"") == "50"
    assert str(global_defaults["season_sekasko_prize_places"]).strip("()'\"") == "3"
    assert str(global_defaults["season_sekasko_prize_coverage"]).strip("()'\"") == "10"
    assert str(policy_defaults["source"]).strip("()'\"") == "purchase"
    for name in (
        "sekasko_prize_rank",
        "sekasko_prize_amount",
        "sekasko_prize_expires_at",
    ):
        assert str(player_defaults[name]).strip("()'\"") == "0"


def _migration_config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.attributes["configure_logger"] = False
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def _strip_head_season_columns(path: Path) -> None:
    with sqlite3.connect(path) as database:
        database.execute("DROP INDEX IF EXISTS uq_deposit_insurance_season_user")
        for table, columns in (
            ("deposit_insurance", ("source_season_id", "source")),
            (
                "weekly_season_players",
                (
                    "sekasko_prize_expires_at",
                    "sekasko_prize_amount",
                    "sekasko_prize_rank",
                ),
            ),
            (
                "global_settings",
                (
                    "season_sekasko_prize_coverage",
                    "season_sekasko_prize_places",
                    "dep_risk_free_principal",
                ),
            ),
        ):
            for column in columns:
                database.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _insert_legacy_global_settings(path: Path) -> None:
    with sqlite3.connect(path) as database:
        columns = [row[1] for row in database.execute("PRAGMA table_info(global_settings)")]
        placeholders = ", ".join("?" for _ in columns)
        for row_id, emission, coverage in ((1, 3, 40), (2, 1, 35), (3, 4, 150)):
            values = {name: 0 for name in columns}
            values.update(
                id=row_id,
                dick_emission_cap=emission,
                sekasko_max_coverage=coverage,
            )
            database.execute(
                f"INSERT INTO global_settings ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({placeholders})",
                [values[name] for name in columns],
            )


def _column_defaults(database: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        str(row[1]): str(row[4] or "").strip("()'\" ")
        for row in database.execute(f"PRAGMA table_info({table})")
    }


def test_0008_migrates_legacy_values_and_is_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "legacy-0007.db"
    config = _migration_config(path)
    command.upgrade(config, "0007_weekly_seasons")
    # Migration 0001 bootstraps missing tables from current metadata. Remove
    # head-only fields to reproduce the shape of a database created by 0007.
    _strip_head_season_columns(path)
    _insert_legacy_global_settings(path)

    command.upgrade(config, "head")

    with sqlite3.connect(path) as database:
        rows = database.execute(
            "SELECT id, dick_emission_cap, sekasko_max_coverage, "
            "dep_risk_free_principal, season_sekasko_prize_places, "
            "season_sekasko_prize_coverage FROM global_settings ORDER BY id"
        ).fetchall()
        assert rows == [
            (1, 2, 100, 50, 3, 10),
            (2, 1, 35, 50, 3, 10),
            (3, 4, 100, 50, 3, 10),
        ]
        assert _column_defaults(database, "deposit_insurance")["source"] == "purchase"
        assert {
            name: _column_defaults(database, "weekly_season_players")[name]
            for name in (
                "sekasko_prize_rank",
                "sekasko_prize_amount",
                "sekasko_prize_expires_at",
            )
        } == {
            "sekasko_prize_rank": "0",
            "sekasko_prize_amount": "0",
            "sekasko_prize_expires_at": "0",
        }

    command.downgrade(config, "0007_weekly_seasons")
    with sqlite3.connect(path) as database:
        defaults = _column_defaults(database, "global_settings")
        assert defaults["dick_emission_cap"] == "3"
        assert defaults["sekasko_max_coverage"] == "40"
        # Downgrade restores schema defaults without guessing at user values.
        assert database.execute(
            "SELECT dick_emission_cap, sekasko_max_coverage FROM global_settings WHERE id = 1"
        ).fetchone() == (2, 100)

    command.upgrade(config, "head")
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_deposit_insurance_season_user'"
        ).fetchone() == (1,)
        assert database.execute(
            "SELECT dick_emission_cap, sekasko_max_coverage, "
            "dep_risk_free_principal, season_sekasko_prize_places, "
            "season_sekasko_prize_coverage FROM global_settings WHERE id = 1"
        ).fetchone() == (2, 100, 50, 3, 10)


def test_season_sekasko_prize_defaults_are_top_three_and_ten() -> None:
    from services import global_settings

    cfg = global_settings._defaults()

    assert cfg.season_sekasko_prize_places == 3
    assert cfg.season_sekasko_prize_coverage == 10
    assert cfg.dick_emission_cap == 2
    assert cfg.sekasko_max_coverage == 100
    assert cfg.dep_risk_free_principal == 50
    assert global_settings.BOUNDS["sekasko_max_coverage"] == (0, 100)
    assert global_settings.BOUNDS["dep_risk_free_principal"] == (0, 100000)
    assert "season_sekasko_prize_places" in global_settings.BOUNDS
    assert "season_sekasko_prize_coverage" in global_settings.BOUNDS
    assert "corp_sanation_days" not in global_settings.DEFAULTS
    assert "corp_sanation_days" not in global_settings.BOUNDS
    assert not hasattr(cfg, "corp_sanation_days")


async def test_global_config_ignores_legacy_sanation_column(db) -> None:
    from repositories import global_settings as settings_repo
    from services import global_settings

    await settings_repo.upsert(corp_sanation_days=90, dick_emission_cap=1)

    cfg = await global_settings.refresh()
    assert cfg.dick_emission_cap == 1
    assert not hasattr(cfg, "corp_sanation_days")


def test_week_boundaries_are_local_and_dst_safe() -> None:
    from services.seasons import week_bounds

    zone = ZoneInfo("Europe/Berlin")
    start, end = week_bounds(ts("2026-03-25T15:00:00", "Europe/Berlin"), zone)

    assert datetime.fromtimestamp(start, zone).isoformat() == "2026-03-23T00:00:00+01:00"
    assert datetime.fromtimestamp(end, zone).isoformat() == "2026-03-30T00:00:00+02:00"
    assert end - start == 167 * 3600


async def test_installation_week_is_partial_and_first_full_week_is_one(db) -> None:
    from repositories import players
    from services import seasons

    chat_id = -8101
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, 11, name="Первый", size=10)

    warmup = await seasons.ensure_live(
        chat_id, now=installed, timezone="UTC", installed_at=installed
    )
    assert warmup.season_number == 0
    assert warmup.is_partial
    assert warmup.tracking_since == installed

    assert (
        await seasons.finalize_due(
            chat_id,
            now=ts("2026-08-17T00:00:00"),
            timezone="UTC",
            installed_at=installed,
        )
        == ()
    )
    live = await seasons.get_live_report(chat_id, now=ts("2026-08-17T00:00:01"))
    assert live is not None
    assert (live.season_number, live.is_partial) == (1, False)
    assert await seasons.list_reports(chat_id) == ()


async def test_timezone_change_uses_first_new_local_monday_without_overlap(db) -> None:
    from repositories import players
    from services import seasons, settings

    chat_id = -8199
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, 99, name="Путешественник", size=10)
    await settings.set_setting(chat_id, "tz", "UTC")
    await seasons.ensure_live(chat_id, now=installed, timezone="UTC", installed_at=installed)
    await seasons.finalize_due(chat_id, now=ts("2026-08-17T00:00:00"))
    await settings.set_setting(chat_id, "tz", "Asia/Tokyo")

    (closed,) = await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))
    live = await seasons.get_live_report(chat_id, now=ts("2026-08-24T00:00:01"))

    assert live is not None
    assert live.starts_at == closed.ends_at
    assert datetime.fromtimestamp(live.ends_at, ZoneInfo("Asia/Tokyo")).isoformat() == (
        "2026-08-31T00:00:00+09:00"
    )


async def test_partial_live_report_ignores_preinstallation_events(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id, user_id = -8111, 12
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, user_id, name="Разогретый", size=13)
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=2,
        size_after=12,
        meta={"game_delta": 2},
        created_at=installed - 86400,
    )
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=1,
        size_after=13,
        meta={"game_delta": 1},
        created_at=installed + 60,
    )

    report = await seasons.ensure_live(
        chat_id, now=installed + 120, timezone="UTC", installed_at=installed
    )

    player = next(value for value in report.players if value.user_id == user_id)
    assert (player.start_length, player.end_length) == (12, 13)
    assert (player.dick_count, player.dick_total) == (1, 1)


async def _start_first_full(chat_id: int, installed: int) -> None:
    from services import seasons

    await seasons.ensure_live(chat_id, now=installed, timezone="UTC", installed_at=installed)
    await seasons.finalize_due(
        chat_id,
        now=ts("2026-08-17T00:00:00"),
        timezone="UTC",
        installed_at=installed,
    )


async def test_finalization_is_idempotent_and_empty_weeks_are_numbered(db) -> None:
    from services import seasons

    chat_id = -8102
    installed = ts("2026-08-12T12:00:00")
    await _start_first_full(chat_id, installed)
    close = ts("2026-08-24T00:00:00")

    first = await seasons.finalize_due(chat_id, now=close)
    second = await seasons.finalize_due(chat_id, now=close)

    assert len(first) == 1
    assert first[0].season_number == 1
    assert first[0].is_empty
    assert first[0].publication_status == seasons.PUBLICATION_SKIPPED
    assert second == ()
    live = await seasons.get_live_report(chat_id, now=close)
    assert live is not None and live.season_number == 2


async def test_duels_without_dick_are_archived_as_empty(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id, user_id = -8110, 21
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, user_id, name="Дуэлянт", size=10)
    await _start_first_full(chat_id, installed)
    await events.log_event(
        chat_id,
        user_id,
        events.DUEL,
        delta=5,
        size_after=15,
        meta={"won": True, "profit": 5},
        created_at=ts("2026-08-18T12:00:00"),
    )
    await players.set_player_fields(chat_id, user_id, size=15)

    (report,) = await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))

    assert report.is_empty
    assert report.publication_status == seasons.PUBLICATION_SKIPPED
    assert report.wealth_leaders[0].wealth_delta == 5


async def test_transfer_events_do_not_create_competitive_wealth(db) -> None:
    from repositories import bank, events, players
    from services import seasons

    chat_id, user_id = -8103, 31
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, user_id, name="Вкладчик", size=10)
    await _start_first_full(chat_id, installed)
    played_at = ts("2026-08-18T12:00:00")
    await players.set_player_fields(chat_id, user_id, size=0)
    await bank.upsert_deposit(chat_id, user_id, principal=10, accrued=0)
    await events.log_event(
        chat_id,
        user_id,
        events.DEPOSIT_OPEN,
        delta=-10,
        size_after=0,
        created_at=played_at,
    )

    (report,) = await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))
    player = next(value for value in report.players if value.user_id == user_id)
    assert player.wealth_delta == 0
    assert (player.start_wealth, player.end_wealth) == (10, 10)
    assert report.is_empty


async def test_scoring_uses_game_delta_days_and_deterministic_ties(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id = -8104
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, 41, name="Младший ID", size=10)
    await players.set_player_fields(chat_id, 42, name="Старший ID", size=10)
    await _start_first_full(chat_id, installed)
    for user_id in (42, 41):
        await events.log_event(
            chat_id,
            user_id,
            events.DICK,
            delta=3,
            size_after=13,
            meta={"rolled": 9, "game_delta": 5, "emitted": 3},
            created_at=ts("2026-08-18T12:00:00"),
        )
        await players.set_player_fields(chat_id, user_id, size=13)

    (report,) = await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))

    assert [value.user_id for value in report.dick_leaders[:2]] == [41, 42]
    assert report.dick_leaders[0].dick_total == 5
    assert report.dick_leaders[0].dick_average == 5
    assert report.dick_leaders[0].active_days == 1
    assert report.best_dick_user_id == 41
    assert report.worst_dick_user_id == 41


async def test_finalization_awards_configured_sekasko_prizes_before_a_deposit(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select

    from db.engine import get_session_factory
    from db.models import Deposit, DepositInsurance, Event
    from repositories import bank as bank_repo
    from repositories import events, players
    from repositories import global_settings as settings_repo
    from services import bank, global_settings, seasons, settings

    chat_id = -8120
    installed = ts("2026-08-12T12:00:00")
    close = ts("2026-08-24T00:00:00")
    expires_at = close + 7 * 86400
    for user_id in (41, 42, 43, 44):
        await players.set_player_fields(chat_id, user_id, name=f"Игрок {user_id}", size=20)
    await settings.set_setting(chat_id, "tz", "UTC")
    await _start_first_full(chat_id, installed)
    for user_id, delta in ((42, 5), (41, 5), (43, 4)):
        await events.log_event(
            chat_id,
            user_id,
            events.DICK,
            delta=delta,
            size_after=20 + delta,
            meta={"game_delta": delta},
            created_at=ts("2026-08-18T12:00:00"),
        )
        await players.set_player_fields(chat_id, user_id, size=20 + delta)
    await settings_repo.upsert(
        season_sekasko_prize_places=3,
        season_sekasko_prize_coverage=12,
        dep_term_days=5,
        sekasko_max_coverage=15,
    )
    await global_settings.refresh()
    await bank_repo.add_insurance(chat_id, 41, 10, 1, expires_at)
    await bank_repo.add_insurance(chat_id, 43, 15, 1, expires_at)

    (report,) = await seasons.finalize_due(chat_id, now=close)

    winners = sorted(
        (value for value in report.players if value.sekasko_prize_rank),
        key=lambda value: value.sekasko_prize_rank,
    )
    assert [(value.user_id, value.sekasko_prize_rank) for value in winners] == [
        (41, 1),
        (42, 2),
    ]
    assert [value.sekasko_prize_amount for value in winners] == [5, 12]
    assert {value.sekasko_prize_expires_at for value in winners} == {expires_at}
    covered = next(value for value in report.players if value.user_id == 43)
    assert (covered.sekasko_prize_rank, covered.sekasko_prize_amount) == (0, 0)

    factory = get_session_factory()
    async with factory() as session:
        policies = list(
            (
                await session.execute(
                    select(DepositInsurance)
                    .where(
                        DepositInsurance.chat_id == chat_id,
                        DepositInsurance.source == "season_prize",
                    )
                    .order_by(DepositInsurance.user_id)
                )
            )
            .scalars()
            .all()
        )
        prizes = list(
            (
                await session.execute(
                    select(Event)
                    .where(Event.chat_id == chat_id, Event.type == "season_sekasko_prize")
                    .order_by(Event.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert await session.get(Deposit, (chat_id, 41)) is None
    assert [value.user_id for value in policies] == [41, 42]
    assert [value.amount for value in policies] == [5, 12]
    assert all(
        value.premium == 0 and value.expires_at == expires_at and value.source_season_id is not None
        for value in policies
    )
    assert [(value.user_id, value.meta["rank"]) for value in prizes] == [(41, 1), (42, 2)]

    monkeypatch.setattr(bank, "_now", lambda: close + 1)
    await bank.open_deposit(chat_id, 41, 5)
    assert await bank.insured_principal(chat_id, 41, 5, now=close + 2) == (5, expires_at)

    assert await seasons.finalize_due(chat_id, now=close + 2) == ()
    async with factory() as session:
        all_policies = list((await session.execute(select(DepositInsurance))).scalars().all())
        all_prizes = list(
            (await session.execute(select(Event).where(Event.type == "season_sekasko_prize")))
            .scalars()
            .all()
        )
    assert len(all_policies) == 4
    assert len(all_prizes) == 2


async def test_season_prize_and_archive_roll_back_together(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import func, select

    from db.engine import get_session_factory
    from db.models import DepositInsurance, Event
    from repositories import events, players
    from services import seasons

    chat_id, user_id = -8121, 51
    installed = ts("2026-08-12T12:00:00")
    close = ts("2026-08-24T00:00:00")
    await players.set_player_fields(chat_id, user_id, name="Лидер", size=10)
    await _start_first_full(chat_id, installed)
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=3,
        size_after=13,
        meta={"game_delta": 3},
        created_at=ts("2026-08-18T12:00:00"),
    )
    await players.set_player_fields(chat_id, user_id, size=13)

    create_live = seasons.repo.create_live_in

    async def fail_advance(*args, **kwargs):
        raise RuntimeError("advance failed")

    monkeypatch.setattr(seasons.repo, "create_live_in", fail_advance)
    with pytest.raises(RuntimeError, match="advance failed"):
        await seasons.finalize_due(chat_id, now=close)

    factory = get_session_factory()
    async with factory() as session:
        policies = await session.scalar(select(func.count()).select_from(DepositInsurance))
        prize_events = await session.scalar(
            select(func.count()).select_from(Event).where(Event.type == "season_sekasko_prize")
        )
    assert policies == 0
    assert prize_events == 0
    live = await seasons.get_live_report(chat_id, now=close)
    assert live is not None and live.status == seasons.STATUS_LIVE

    monkeypatch.setattr(seasons.repo, "create_live_in", create_live)
    (report,) = await seasons.finalize_due(chat_id, now=close)
    assert report.players[0].sekasko_prize_amount == 10


async def test_season_prize_event_does_not_create_economy_activity(db) -> None:
    from repositories import events
    from services import economy_metrics

    now = ts("2026-08-24T00:00:00")
    await events.log_event(
        -8124,
        71,
        events.DICK,
        delta=3,
        size_after=13,
        created_at=now - 60,
    )
    await events.log_event(
        -8124,
        72,
        events.SEASON_SEKASKO_PRIZE,
        meta={"coverage": 10},
        created_at=now - 30,
    )

    snapshot = await economy_metrics.snapshot(now=now)

    assert snapshot.active_7d == 1
    assert snapshot.active_30d == 1
    assert snapshot.net_delta_7d == 3
    assert snapshot.net_delta_30d == 3


async def test_finalization_serializes_prize_with_sekasko_purchase(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import func, select

    from db.engine import get_session_factory
    from db.models import DepositInsurance
    from repositories import events, players
    from repositories import global_settings as settings_repo
    from services import bank, global_settings, seasons, settings

    chat_id, user_id = -8125, 81
    installed = ts("2026-08-12T12:00:00")
    close = ts("2026-08-24T00:00:00")
    monkeypatch.setattr(bank, "_now", lambda: close)
    await players.set_player_fields(chat_id, user_id, name="Победитель", size=200)
    await settings.set_setting(chat_id, "tz", "UTC")
    await _start_first_full(chat_id, installed)
    await bank.open_deposit(chat_id, user_id, 100)
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=3,
        size_after=103,
        meta={"game_delta": 3},
        created_at=ts("2026-08-18T12:00:00"),
    )
    await players.set_player_fields(chat_id, user_id, size=103)
    await settings_repo.upsert(
        season_sekasko_prize_places=1,
        season_sekasko_prize_coverage=10,
        sekasko_max_coverage=10,
    )
    await global_settings.refresh()

    entered = asyncio.Event()
    release = asyncio.Event()
    finalize_in = seasons.repo.finalize_in

    async def paused_finalize(*args, **kwargs):
        entered.set()
        await release.wait()
        return await finalize_in(*args, **kwargs)

    monkeypatch.setattr(seasons.repo, "finalize_in", paused_finalize)
    finalization = asyncio.create_task(seasons.finalize_due(chat_id, now=close))
    await asyncio.wait_for(entered.wait(), timeout=2)
    purchase = asyncio.create_task(bank.buy_sekasko(chat_id, user_id, 1))
    await asyncio.sleep(0)
    try:
        assert not purchase.done()
    finally:
        release.set()

    (report,) = await asyncio.wait_for(finalization, timeout=2)
    assert report.players[0].sekasko_prize_amount == 10
    with pytest.raises(bank.BankError, match="insurance_limit"):
        await asyncio.wait_for(purchase, timeout=2)

    factory = get_session_factory()
    async with factory() as session:
        active = await session.scalar(
            select(func.coalesce(func.sum(DepositInsurance.amount), 0)).where(
                DepositInsurance.chat_id == chat_id,
                DepositInsurance.user_id == user_id,
                DepositInsurance.expires_at > close,
            )
        )
    assert active == 10


async def test_negative_ties_award_exactly_the_configured_top_three(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id = -8122
    installed = ts("2026-08-12T12:00:00")
    for user_id in (12, 11, 14, 13):
        await players.set_player_fields(chat_id, user_id, name=f"Игрок {user_id}", size=10)
    await _start_first_full(chat_id, installed)
    for user_id, delta in ((12, -1), (11, -1), (14, -3), (13, -2)):
        await events.log_event(
            chat_id,
            user_id,
            events.DICK,
            delta=delta,
            size_after=10 + delta,
            meta={"game_delta": delta},
            created_at=ts("2026-08-18T12:00:00"),
        )
        await players.set_player_fields(chat_id, user_id, size=10 + delta)

    (report,) = await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))

    prizes = sorted(
        (value for value in report.players if value.sekasko_prize_rank),
        key=lambda value: value.sekasko_prize_rank,
    )
    assert [(value.user_id, value.dick_total) for value in report.dick_leaders] == [
        (11, -1),
        (12, -1),
        (13, -2),
        (14, -3),
    ]
    assert [(value.user_id, value.sekasko_prize_rank) for value in prizes] == [
        (11, 1),
        (12, 2),
        (13, 3),
    ]
    assert {value.sekasko_prize_amount for value in prizes} == {10}


async def test_catch_up_skips_expired_prizes_and_does_not_stack(db) -> None:
    from sqlalchemy import select

    from db.engine import get_session_factory
    from db.models import DepositInsurance
    from repositories import events, players
    from services import seasons, settings

    chat_id, user_id = -8123, 61
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, user_id, name="Опоздавший", size=10)
    await settings.set_setting(chat_id, "tz", "UTC")
    await _start_first_full(chat_id, installed)
    for played_at in (
        "2026-08-18T12:00:00",
        "2026-08-25T12:00:00",
        "2026-09-01T12:00:00",
    ):
        await events.log_event(
            chat_id,
            user_id,
            events.DICK,
            delta=1,
            size_after=11,
            meta={"game_delta": 1},
            created_at=ts(played_at),
        )
    await players.set_player_fields(chat_id, user_id, size=13)

    reports = await seasons.finalize_due(chat_id, now=ts("2026-09-07T00:00:00"))

    assert [value.season_number for value in reports] == [1, 2, 3]
    assert [
        next(player for player in value.players if player.user_id == user_id).sekasko_prize_amount
        for value in reports
    ] == [0, 0, 10]
    factory = get_session_factory()
    async with factory() as session:
        policies = list(
            (
                await session.execute(
                    select(DepositInsurance).where(
                        DepositInsurance.chat_id == chat_id,
                        DepositInsurance.source == "season_prize",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(policies) == 1
    assert policies[0].expires_at == ts("2026-09-14T00:00:00")


async def test_admin_and_baseline_change_snapshot_but_not_competition(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id, user_id = -8105, 51
    installed = ts("2026-08-12T12:00:00")
    await _start_first_full(chat_id, installed)
    await players.set_player_fields(chat_id, user_id, name="Новенький", size=20)
    moment = ts("2026-08-18T12:00:00")
    await events.log_event(chat_id, user_id, events.BASELINE, size_after=7, created_at=moment)
    await events.log_event(
        chat_id,
        user_id,
        events.ADMIN_ADJUST,
        delta=13,
        size_after=20,
        created_at=moment + 1,
    )

    (report,) = await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))
    player = next(value for value in report.players if value.user_id == user_id)
    assert (player.start_wealth, player.end_wealth) == (0, 20)
    assert player.wealth_delta == 0
    assert report.wealth_delta == 20
    assert report.is_empty


async def test_finalized_snapshot_and_name_are_immutable(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id, user_id = -8106, 61
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, user_id, name="Старое имя", size=10)
    await _start_first_full(chat_id, installed)
    event_at = ts("2026-08-18T12:00:00")
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=4,
        size_after=14,
        meta={"game_delta": 4},
        created_at=event_at,
    )
    await players.set_player_fields(chat_id, user_id, size=14)
    await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))
    frozen = await seasons.get_report(chat_id, 1)
    assert frozen is not None

    await players.set_player_fields(chat_id, user_id, name="Новое имя", size=113)
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=99,
        size_after=113,
        meta={"game_delta": 99},
        created_at=event_at + 1,
    )
    unchanged = await seasons.get_report(chat_id, 1)

    assert unchanged == frozen
    assert unchanged.players[0].name == "Старое имя"
    assert unchanged.players[0].dick_total == 4


async def test_catch_up_archives_every_missed_week(db) -> None:
    from services import seasons

    chat_id = -8107
    installed = ts("2026-08-12T12:00:00")
    await _start_first_full(chat_id, installed)

    finalized = await seasons.finalize_due(chat_id, now=ts("2026-09-07T00:00:00"))

    assert [value.season_number for value in finalized] == [1, 2, 3]
    assert all(value.is_empty for value in finalized)
    live = await seasons.get_live_report(chat_id, now=ts("2026-09-07T00:00:00"))
    assert live is not None and live.season_number == 4


async def test_mark_published_only_changes_pending_nonempty_season(db) -> None:
    from repositories import events, players
    from services import seasons

    chat_id, user_id = -8108, 81
    installed = ts("2026-08-12T12:00:00")
    await players.set_player_fields(chat_id, user_id, name="Игрок", size=1)
    await _start_first_full(chat_id, installed)
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=1,
        size_after=2,
        meta={"game_delta": 1},
        created_at=ts("2026-08-18T12:00:00"),
    )
    await players.set_player_fields(chat_id, user_id, size=2)
    await seasons.finalize_due(chat_id, now=ts("2026-08-24T00:00:00"))

    assert await seasons.mark_published(chat_id, 1, message_id=123, published_at=999)
    assert not await seasons.mark_published(chat_id, 1, message_id=456, published_at=1000)
    report = await seasons.get_report(chat_id, 1)
    assert report is not None
    assert (report.publication_status, report.published_message_id) == ("published", 123)


async def test_active_timezone_is_snapshotted(db) -> None:
    from services import seasons

    chat_id = -8109
    installed = ts("2026-08-12T12:00:00")
    first = await seasons.ensure_live(
        chat_id, now=installed, timezone="UTC", installed_at=installed
    )
    second = await seasons.ensure_live(
        chat_id, now=installed + 60, timezone="Europe/Moscow", installed_at=installed
    )

    assert first.timezone == second.timezone == "UTC"
    assert (first.starts_at, first.ends_at) == (second.starts_at, second.ends_at)
