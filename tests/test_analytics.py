from __future__ import annotations

import os
import tempfile
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
        meta={"rolled": 5, "emitted": 3, "clipped": 0},
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
    assert sum(data.values) == 3
    assert (await analytics.render_png(data)).startswith(b"\x89PNG\r\n\x1a\n")
    csv_data = analytics.render_csv(data).decode("utf-8-sig")
    assert "раздел;Рост" in csv_data
    assert "ИТОГО;3.0" in csv_data


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
    bot = AsyncMock()
    assert await stats_handler._allowed(bot, 71, analytics.Scope("user", -7001, 71))
    assert not await stats_handler._allowed(bot, 72, analytics.Scope("user", -7001, 71))
    assert not await stats_handler._allowed(bot, 72, analytics.Scope("chat", -7001, 0))
    assert not stats_handler._section_allowed(analytics.Scope("personal", user_id=71), "corp")
    assert stats_handler._section_allowed(analytics.Scope("chat", chat_id=-7001), "corp")


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
