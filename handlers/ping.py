from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("ping"))
async def cmd_ping(message: Message, bot: Bot) -> None:
    processing_start = message.date.astimezone(timezone.utc)
    now_before = datetime.now(timezone.utc)

    sent = await message.answer("Pong!")

    api_ms = int((datetime.now(timezone.utc) - now_before).total_seconds() * 1000)
    total_ms = int((datetime.now(timezone.utc) - processing_start).total_seconds() * 1000)

    await sent.edit_text(f"Pong! {api_ms}ms (API RTT), total {total_ms}ms")
