"""Finalize weekly seasons and publish configured chat-local reports."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import BufferedInputFile

from presentation import seasons as season_view
from repositories import chats as chats_repo
from repositories import threads as threads_repo
from services import season_chart, seasons, settings

logger = logging.getLogger(__name__)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def _publication_due(
    report: seasons.SeasonReport,
    *,
    now: int,
    weekday: int,
    hour: int,
) -> bool:
    zone = _zone(report.timezone)
    closed = datetime.fromtimestamp(report.ends_at, zone)
    scheduled = (closed + timedelta(days=(weekday - closed.weekday()) % 7)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return datetime.fromtimestamp(now, zone) >= scheduled


async def _run_chat(bot: Bot, chat_id: int, *, now: int) -> int:
    effective = await settings.get_effective(chat_id)
    await seasons.finalize_due(chat_id, now=now, timezone=effective.tz)

    pending = await seasons.pending_reports(chat_id)
    if len(pending) > 1:
        await seasons.skip_publication(
            chat_id, tuple(report.season_number for report in pending[1:])
        )
    if not pending or not effective.stats_digest_enabled:
        return 0

    newest = pending[0]
    if not _publication_due(
        newest,
        now=now,
        weekday=effective.stats_digest_weekday,
        hour=effective.stats_digest_hour,
    ):
        return 0

    png = await season_chart.render_png(newest)
    thread_id, reason = await threads_repo.resolve_thread(chat_id)
    if reason != "explicit":
        thread_id = None
    sent = await bot.send_photo(
        chat_id,
        BufferedInputFile(png, filename=f"season-{newest.season_number}.png"),
        caption=season_view.caption(newest),
        message_thread_id=thread_id,
        parse_mode="HTML",
    )
    marked = await seasons.mark_published(
        chat_id,
        newest.season_number,
        message_id=int(sent.message_id),
        published_at=now,
    )
    return int(marked)


async def run_once(bot: Bot, *, now: int | None = None) -> int:
    sent = 0
    timestamp = int(time.time()) if now is None else now
    for chat_id in await chats_repo.chat_ids_by_mode("groups"):
        try:
            sent += await _run_chat(bot, chat_id, now=timestamp)
        except Exception:
            logger.exception("Could not process weekly season", extra={"chat_id": chat_id})
    return sent


async def loop(bot: Bot) -> None:
    while True:
        try:
            await run_once(bot)
        except Exception:
            logger.exception("Weekly season scheduler pass failed")
        await asyncio.sleep(60)
