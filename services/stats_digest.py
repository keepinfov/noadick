from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.types import BufferedInputFile

from repositories import chat_settings as settings_repo
from repositories import threads as threads_repo
from services import analytics

logger = logging.getLogger(__name__)


async def run_once(bot: Bot) -> int:
    sent = 0
    for row in await settings_repo.enabled_digests():
        try:
            zone = ZoneInfo(row.tz or "Europe/Moscow")
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Europe/Moscow")
        now = datetime.now(zone)
        iso = now.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        if (
            now.weekday() != int(row.stats_digest_weekday)
            or now.hour < int(row.stats_digest_hour)
            or row.stats_digest_last_week == week_key
        ):
            continue
        try:
            data = await analytics.dashboard(
                analytics.Scope("chat", chat_id=row.chat_id), "overview", "7"
            )
            leaders = await analytics.dashboard(
                analytics.Scope("chat", chat_id=row.chat_id), "leaders", "7"
            )
            png = await analytics.render_png(data)
            thread_id, thread_reason = await threads_repo.resolve_thread(row.chat_id)
            if thread_reason != "explicit":
                thread_id = None
            podium = [
                f"• {html.escape(label)}: <b>{html.escape(value)}</b>"
                for label, value in leaders.metrics
                if label.startswith("#")
            ]
            digest_caption = "📊 <b>Недельный отчёт</b>\n\n" + analytics.caption(data)
            if podium:
                podium_text = "\n\n🏆 <b>Лучшие за неделю</b>\n" + "\n".join(podium)
                if len(digest_caption + podium_text) <= 1024:
                    digest_caption += podium_text
            await bot.send_photo(
                row.chat_id,
                BufferedInputFile(png, filename="weekly-stats.png"),
                caption=digest_caption,
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Could not send weekly stats digest", extra={"chat_id": row.chat_id})
            continue
        await settings_repo.mark_digest_sent(row.chat_id, week_key)
        sent += 1
    return sent


async def loop(bot: Bot) -> None:
    while True:
        try:
            await run_once(bot)
        except Exception:
            logger.exception("Weekly stats scheduler pass failed")
        await asyncio.sleep(60)
