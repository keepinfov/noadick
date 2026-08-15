from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import Message

from callbacks import SeasonCallback
from services.seasons import PlayerReport, SeasonReport


def report(
    *,
    number: int = 3,
    status: str = "finalized",
    partial: bool = False,
    empty: bool = False,
    players: tuple[PlayerReport, ...] | None = None,
) -> SeasonReport:
    values = players or (
        PlayerReport(1, "<Вася>", 10, 18, 10, 30, 8, 2, 2, 7, 1, 20),
        PlayerReport(2, "Петя", 20, 14, 20, 12, -6, 1, 1, -6, -6, -8),
    )
    return SeasonReport(
        chat_id=-1007001,
        season_number=number,
        timezone="UTC",
        starts_at=1_776_038_400,
        ends_at=1_776_643_200,
        tracking_since=1_776_038_400,
        status=status,
        is_partial=partial,
        is_empty=empty,
        publication_status="pending",
        published_at=0,
        published_message_id=0,
        length_start=30,
        length_end=32,
        wealth_start=30,
        wealth_end=42,
        emission=3,
        best_dick_delta=7,
        best_dick_user_id=1,
        worst_dick_delta=-6,
        worst_dick_user_id=2,
        players=values,
    )


def test_season_callback_payload_fits_telegram_limit() -> None:
    payload = SeasonCallback(
        action="f999",
        chat_id=-9_223_372_036_854_775_808,
        season_no=2_147_483_647,
        owner_id=9_223_372_036_854_775_807,
    ).pack()

    assert len(payload.encode()) <= 64
    assert SeasonCallback.unpack(payload).season_no == 2_147_483_647


def test_publication_due_respects_configured_local_time() -> None:
    from services import stats_digest

    zone = ZoneInfo("Asia/Tokyo")
    value = replace(
        report(),
        timezone=zone.key,
        ends_at=int(datetime(2026, 8, 17, 0, tzinfo=zone).timestamp()),
    )
    before = int(datetime(2026, 8, 19, 9, 59, tzinfo=zone).timestamp())
    at_time = int(datetime(2026, 8, 19, 10, 0, tzinfo=zone).timestamp())

    assert not stats_digest._publication_due(value, now=before, weekday=2, hour=10)
    assert stats_digest._publication_due(value, now=at_time, weekday=2, hour=10)


def test_season_caption_is_safe_bounded_and_handles_empty() -> None:
    from presentation import seasons as view

    value = view.caption(report())
    empty = view.caption(report(empty=True, players=()))

    assert "&lt;Вася&gt;" in value
    assert "<Вася>" not in value
    assert "Инфляция сантиметра" in value
    assert len(value) <= 1024
    assert "Пустая неделя" in empty


def test_full_season_table_pages_combined_metrics() -> None:
    from presentation import seasons as view

    players = tuple(
        PlayerReport(
            index,
            f"Игрок {index}",
            10,
            10 + index,
            10,
            10 + index,
            index,
            1,
            1,
            index,
            index,
            index,
        )
        for index in range(1, 13)
    )
    value = report(players=players)
    caption = view.caption(value, full=True, page=1)
    keyboard = view.keyboard(value, chat_id=value.chat_id, owner_id=71, full=True, page=1)

    assert "Общая таблица · 2/2" in caption
    assert "/dick: итог · среднее · дни | состояние" in caption
    assert len(caption) <= 1024
    actions = [
        SeasonCallback.unpack(button.callback_data).action
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert "f0" in actions


async def test_season_chart_handles_negative_and_empty_panels() -> None:
    from services import season_chart

    assert (await season_chart.render_png(report())).startswith(b"\x89PNG\r\n\x1a\n")
    assert (await season_chart.render_png(report(empty=True, players=()))).startswith(
        b"\x89PNG\r\n\x1a\n"
    )


async def test_season_command_is_group_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from handlers import season

    private = SimpleNamespace(
        chat=SimpleNamespace(id=1, type="private"),
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    await season.cmd_season(private)
    private.answer.assert_awaited_once()

    group = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type="supergroup"),
        from_user=SimpleNamespace(id=71),
        answer_photo=AsyncMock(),
    )
    monkeypatch.setattr(
        season.seasons, "ensure_live", AsyncMock(return_value=report(status="live"))
    )
    monkeypatch.setattr(season.season_chart, "render_png", AsyncMock(return_value=b"png"))
    monkeypatch.setattr(season, "_navigation", AsyncMock(return_value=(False, False)))
    await season.cmd_season(group)
    group.answer_photo.assert_awaited_once()


async def test_season_callback_rejects_foreign_owner_and_chat() -> None:
    from handlers import season

    message = MagicMock(spec=Message)
    message.chat = SimpleNamespace(id=-1007001, type="supergroup")
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=72),
        message=message,
        answer=AsyncMock(),
    )
    data = SeasonCallback(action="s", chat_id=-1007001, season_no=3, owner_id=71)
    await season.season_callback(callback, data)
    assert callback.answer.await_args.kwargs["show_alert"] is True

    callback.from_user.id = 71
    data = SeasonCallback(action="s", chat_id=-1007002, season_no=3, owner_id=71)
    await season.season_callback(callback, data)
    assert callback.answer.await_count == 2


async def test_season_callback_navigation_and_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    from handlers import season

    message = MagicMock(spec=Message)
    message.chat = SimpleNamespace(id=-1007001, type="supergroup")
    message.edit_media = AsyncMock()
    message.edit_caption = AsyncMock()
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=71), message=message, answer=AsyncMock()
    )
    current = report(number=3, status="live")
    older = report(number=2)
    monkeypatch.setattr(season, "_selected", AsyncMock(return_value=current))
    monkeypatch.setattr(season, "_nearest_archived", AsyncMock(return_value=older))
    monkeypatch.setattr(season, "_navigation", AsyncMock(return_value=(True, False)))
    monkeypatch.setattr(season.season_chart, "render_png", AsyncMock(return_value=b"png"))

    await season.season_callback(
        callback, SeasonCallback(action="p", chat_id=-1007001, season_no=3, owner_id=71)
    )
    message.edit_media.assert_awaited_once()

    message.edit_media.reset_mock()
    await season.season_callback(
        callback, SeasonCallback(action="r", chat_id=-1007001, season_no=3, owner_id=71)
    )
    message.edit_media.assert_awaited_once()


async def test_newer_navigation_returns_live_after_latest_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from handlers import season

    archived = report(number=4)
    live = report(number=5, status="live")
    monkeypatch.setattr(season.seasons, "list_reports", AsyncMock(return_value=(archived,)))
    monkeypatch.setattr(season.seasons, "get_live_report", AsyncMock(return_value=live))

    assert await season._nearest_archived(live.chat_id, 4, newer=True) == live


async def test_scheduler_finalizes_disabled_chat_without_publishing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import stats_digest

    monkeypatch.setattr(
        stats_digest.settings,
        "get_effective",
        AsyncMock(
            return_value=SimpleNamespace(
                tz="UTC",
                stats_digest_enabled=False,
                stats_digest_weekday=0,
                stats_digest_hour=10,
            )
        ),
    )
    finalize = AsyncMock(return_value=())
    monkeypatch.setattr(stats_digest.seasons, "finalize_due", finalize)
    monkeypatch.setattr(stats_digest.seasons, "pending_reports", AsyncMock(return_value=()))
    bot = AsyncMock()

    assert await stats_digest._run_chat(bot, -1007001, now=1_800_000_000) == 0
    finalize.assert_awaited_once()
    bot.send_photo.assert_not_awaited()


async def test_scheduler_catches_up_skips_older_and_uses_explicit_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import stats_digest

    newest, older = report(number=4), report(number=3)
    effective = SimpleNamespace(
        tz="UTC",
        stats_digest_enabled=True,
        stats_digest_weekday=0,
        stats_digest_hour=0,
    )
    monkeypatch.setattr(stats_digest.settings, "get_effective", AsyncMock(return_value=effective))
    monkeypatch.setattr(stats_digest.seasons, "finalize_due", AsyncMock(return_value=()))
    monkeypatch.setattr(
        stats_digest.seasons, "pending_reports", AsyncMock(return_value=(newest, older))
    )
    skip = AsyncMock(return_value=1)
    monkeypatch.setattr(stats_digest.seasons, "skip_publication", skip)
    monkeypatch.setattr(stats_digest.season_chart, "render_png", AsyncMock(return_value=b"png"))
    monkeypatch.setattr(
        stats_digest.threads_repo, "resolve_thread", AsyncMock(return_value=(77, "explicit"))
    )
    monkeypatch.setattr(stats_digest.seasons, "mark_published", AsyncMock(return_value=True))
    bot = AsyncMock()
    bot.send_photo.return_value = SimpleNamespace(message_id=99)

    assert await stats_digest._run_chat(bot, newest.chat_id, now=1_800_000_000) == 1
    skip.assert_awaited_once_with(newest.chat_id, (older.season_number,))
    assert bot.send_photo.await_args.kwargs["message_thread_id"] == 77
    assert bot.send_photo.await_args.kwargs.get("reply_markup") is None


async def test_scheduler_retries_send_and_marks_only_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services import stats_digest

    value = report(number=4)
    effective = SimpleNamespace(
        tz="UTC",
        stats_digest_enabled=True,
        stats_digest_weekday=0,
        stats_digest_hour=0,
    )
    monkeypatch.setattr(stats_digest.settings, "get_effective", AsyncMock(return_value=effective))
    monkeypatch.setattr(stats_digest.seasons, "finalize_due", AsyncMock(return_value=()))
    monkeypatch.setattr(stats_digest.seasons, "pending_reports", AsyncMock(return_value=(value,)))
    monkeypatch.setattr(stats_digest.season_chart, "render_png", AsyncMock(return_value=b"png"))
    monkeypatch.setattr(
        stats_digest.threads_repo, "resolve_thread", AsyncMock(return_value=(44, "auto"))
    )
    marked = AsyncMock(return_value=True)
    monkeypatch.setattr(stats_digest.seasons, "mark_published", marked)
    bot = AsyncMock()
    bot.send_photo.side_effect = [RuntimeError("telegram down"), SimpleNamespace(message_id=101)]

    with pytest.raises(RuntimeError, match="telegram down"):
        await stats_digest._run_chat(bot, value.chat_id, now=1_800_000_000)
    marked.assert_not_awaited()

    assert await stats_digest._run_chat(bot, value.chat_id, now=1_800_000_000) == 1
    marked.assert_awaited_once()
    assert bot.send_photo.await_args.kwargs["message_thread_id"] is None


async def test_scheduler_isolates_chat_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import stats_digest

    monkeypatch.setattr(
        stats_digest.chats_repo, "chat_ids_by_mode", AsyncMock(return_value=[-1, -2])
    )
    run = AsyncMock(side_effect=[RuntimeError("boom"), 1])
    monkeypatch.setattr(stats_digest, "_run_chat", run)

    assert await stats_digest.run_once(AsyncMock(), now=1_800_000_000) == 1
    assert run.await_count == 2
