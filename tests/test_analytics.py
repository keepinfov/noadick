from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path
    from db import engine as engine_mod
    from services import settings

    await engine_mod.dispose_engine()
    settings.invalidate(-7001)
    await engine_mod.init_db()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        os.unlink(path)


async def test_growth_dashboard_png_and_csv(db) -> None:
    from repositories import events, players
    from services import analytics

    chat_id, user_id = -7001, 71
    now = 1_800_000_000
    await players.set_player_fields(chat_id, user_id, name="Тестер", size=13)
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=5,
        size_after=15,
        meta={"rolled": 9, "game_delta": 5, "emitted": 3, "clipped": 0},
        created_at=now - 3600,
    )
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=-2,
        size_after=13,
        meta={"rolled": -2, "roll_debt": 2},
        created_at=now - 60,
    )

    data = await analytics.dashboard(
        analytics.Scope("user", chat_id=chat_id, user_id=user_id),
        "growth",
        "7",
        now=now,
    )

    assert ("Нажатий", "2") in data.metrics
    assert ("Чистый итог", "+3 см") in data.metrics
    assert ("Средний бросок", "+1.5 см") in data.metrics
    assert data.chart_kind == "line"
    assert data.series[0][0] == "Чистое состояние"
    assert data.series[0][1][-1] == 13
    assert "статистическая пустыня" not in analytics.caption(data)
    assert (await analytics.render_png(data)).startswith(b"\x89PNG\r\n\x1a\n")
    csv_data = analytics.render_csv(data).decode("utf-8-sig")
    assert "раздел;Рост" in csv_data
    assert "интервал;Чистое состояние" in csv_data
    assert "ПОСЛЕДНЕЕ;13.0" in csv_data


async def test_dashboard_coalesces_identical_concurrent_requests(db, monkeypatch) -> None:
    from services import analytics

    analytics._dashboard_cache.clear()
    analytics._dashboard_flights.clear()
    calls = 0

    async def build(scope, section, period, *, now=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return analytics.Dashboard("Чат", section, period, [], [], [], "Тест")

    monkeypatch.setattr(analytics, "_dashboard_uncached", build)
    scope = analytics.Scope("leaderboard", chat_id=-7001)
    first, second = await asyncio.gather(
        analytics.dashboard(scope, "leaders", "7"),
        analytics.dashboard(scope, "leaders", "7"),
    )
    assert calls == 1
    assert first is second


async def test_chat_leaders_are_combined_into_overtake_chart(db) -> None:
    from repositories import events, players
    from services import analytics

    chat_id = -7001
    now = 1_800_000_000
    await players.set_player_fields(chat_id, 71, name="Первый", size=20)
    await players.set_player_fields(chat_id, 72, name="Второй", size=30)
    await events.log_event(chat_id, 71, events.BASELINE, size_after=10, created_at=now - 6 * 86400)
    await events.log_event(chat_id, 72, events.BASELINE, size_after=15, created_at=now - 6 * 86400)
    await events.log_event(
        chat_id, 71, events.DICK, delta=30, size_after=40, created_at=now - 4 * 86400
    )
    await events.log_event(
        chat_id, 72, events.DICK, delta=15, size_after=30, created_at=now - 3 * 86400
    )
    await events.log_event(
        chat_id, 71, events.DICK, delta=-20, size_after=20, created_at=now - 2 * 86400
    )

    data = await analytics.dashboard(
        analytics.Scope("chat", chat_id=chat_id), "leaders", "7", now=now
    )

    assert data.chart_kind == "line"
    assert [name for name, _values in data.series] == ["Второй", "Первый"]
    second = data.series[0][1]
    first = data.series[1][1]
    assert any(
        first_value > second_value for first_value, second_value in zip(first, second, strict=True)
    )
    assert second[-1] == 30
    assert first[-1] == 20
    assert (await analytics.render_png(data)).startswith(b"\x89PNG\r\n\x1a\n")
    csv_data = analytics.render_csv(data).decode("utf-8-sig")
    assert "интервал;Второй;Первый" in csv_data


async def test_chat_growth_is_total_size_timeline(db) -> None:
    from repositories import events, players
    from services import analytics

    chat_id = -7003
    now = 1_800_000_000
    await players.set_player_fields(chat_id, 81, name="Один", size=20)
    await players.set_player_fields(chat_id, 82, name="Два", size=30)
    await events.log_event(chat_id, 81, events.DICK, delta=5, size_after=20, created_at=now - 3600)
    await events.log_event(chat_id, 82, events.DICK, delta=10, size_after=30, created_at=now - 1800)

    data = await analytics.dashboard(
        analytics.Scope("chat", chat_id=chat_id), "growth", "d", now=now
    )

    assert data.chart_kind == "line"
    assert data.series[0][0] == "Общее чистое состояние"
    assert data.series[0][1][-1] == 50
    assert "статистическая пустыня" not in analytics.caption(data)


async def test_wealth_includes_deposit_poker_and_debt(db) -> None:
    from db.engine import get_session_factory
    from db.models import PokerSeat, PokerTable
    from repositories import bank, players
    from services import wealth

    chat_id = -7004
    user_id = 91
    await players.set_player_fields(chat_id, user_id, name="Капиталист", size=40)
    await players.set_player_fields(chat_id, 92, name="Только наличка", size=63)
    await bank.upsert_deposit(chat_id, user_id, principal=25, accrued=3)
    await bank.upsert_loan(chat_id, user_id, principal=12, accrued_interest=2)
    factory = get_session_factory()
    async with factory() as session:
        session.add(PokerTable(table_id="wealth01", chat_id=chat_id, host_id=user_id))
        session.add(
            PokerSeat(
                table_id="wealth01",
                user_id=user_id,
                seat_no=0,
                name="Капиталист",
                stack=9,
                committed=1,
            )
        )
        await session.commit()

    rows = await wealth.chat_rows(chat_id)
    row = rows[0]

    assert [value.user_id for value in rows] == [91, 92]
    assert (row.liquid, row.deposits, row.poker, row.debt) == (40, 28, 10, 14)
    assert row.net == 64


async def test_growth_timeline_recovers_from_events_without_player(db) -> None:
    from repositories import events
    from services import analytics

    chat_id = -7002
    user_id = 73
    now = 1_800_000_000
    await events.log_event(
        chat_id,
        user_id,
        events.DICK,
        delta=7,
        size_after=17,
        meta={"rolled": 7},
        created_at=now - 3600,
    )

    data = await analytics.dashboard(
        analytics.Scope("user", chat_id=chat_id, user_id=user_id),
        "growth",
        "d",
        now=now,
    )

    assert data.labels
    assert data.series == [("Чистое состояние", data.series[0][1])]
    assert data.series[0][1][-1] == 17
    assert "статистическая пустыня" not in analytics.caption(data)


async def test_corporation_movements_are_audited(db) -> None:
    from db.engine import get_session_factory
    from db.models import CorporationLedger
    from repositories import bank

    await bank.corp_apply(
        -7001,
        delta=25,
        reason="duel_tax",
        user_id=71,
        meta={"duel": "abc"},
        tax=25,
    )

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(CorporationLedger).where(CorporationLedger.chat_id == -7001)
            )
        ).scalar_one()
    assert row.reason == "duel_tax"
    assert row.user_id == 71
    assert row.cash_delta == 25
    assert row.balance_after == 25


async def test_digest_settings_wrap_and_persist(db) -> None:
    from repositories import chat_settings as repo
    from services import settings

    assert await settings.toggle_stats_digest(-7001) is True
    assert await settings.adjust_stats_digest(-7001, "weekday", -1) == 6
    assert await settings.adjust_stats_digest(-7001, "hour", -11) == 23
    row = await repo.get_settings(-7001)
    assert row is not None
    assert row.stats_digest_enabled is True
    assert row.stats_digest_weekday == 6
    assert row.stats_digest_hour == 23


async def test_dashboard_authorization_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from handlers import stats as stats_handler
    from services import analytics

    monkeypatch.setattr(stats_handler, "is_chat_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(stats_handler, "is_global_admin", lambda _user_id: False)
    monkeypatch.setattr(
        stats_handler.chats_repo, "get_chat", AsyncMock(return_value=SimpleNamespace(type="group"))
    )
    bot = AsyncMock()
    assert await stats_handler._allowed(bot, 71, analytics.Scope("user", -7001, 71))
    assert not await stats_handler._allowed(bot, 72, analytics.Scope("user", -7001, 71))
    assert not await stats_handler._allowed(bot, 72, analytics.Scope("chat", -7001, 0))
    assert not stats_handler._section_allowed(analytics.Scope("personal", user_id=71), "corp")
    assert stats_handler._section_allowed(analytics.Scope("chat", chat_id=-7001), "corp")
    public_scope = analytics.Scope("leaderboard", chat_id=-7001)
    assert await stats_handler._allowed(bot, 72, public_scope)
    assert stats_handler._section_allowed(public_scope, "leaders")
    assert not stats_handler._section_allowed(public_scope, "bank")
    stats_handler.chats_repo.get_chat.return_value = SimpleNamespace(type="private")
    assert not await stats_handler._allowed(bot, 72, public_scope)


async def test_weekly_digest_is_not_duplicated(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from repositories import chat_settings
    from services import stats_digest

    now = datetime.now(UTC)
    await chat_settings.upsert_settings(
        -7001,
        tz="UTC",
        stats_digest_enabled=True,
        stats_digest_weekday=now.weekday(),
        stats_digest_hour=now.hour,
    )
    monkeypatch.setattr(stats_digest.analytics, "render_png", AsyncMock(return_value=b"png"))
    bot = AsyncMock()

    assert await stats_digest.run_once(bot) == 1
    assert await stats_digest.run_once(bot) == 0
    assert bot.send_photo.await_count == 1
