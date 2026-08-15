from __future__ import annotations

import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    from db import engine as engine_mod
    from services import settings

    await engine_mod.dispose_engine()
    settings._cache.clear()
    await engine_mod.init_db()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        settings._cache.clear()
        os.unlink(path)


def ts(value: str, zone: str = "UTC") -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(zone)).timestamp())


async def test_migration_creates_season_constraints_and_indexes(db) -> None:
    from db.engine import get_engine

    async with get_engine().connect() as connection:
        tables, season_indexes, player_indexes = await connection.run_sync(
            lambda sync: (
                set(inspect(sync).get_table_names()),
                {value["name"] for value in inspect(sync).get_indexes("weekly_seasons")},
                {value["name"] for value in inspect(sync).get_indexes("weekly_season_players")},
            )
        )
    assert {"weekly_seasons", "weekly_season_players"} <= tables
    assert "uq_weekly_seasons_live_chat" in season_indexes
    assert "ix_weekly_season_players_dick" in player_indexes


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
