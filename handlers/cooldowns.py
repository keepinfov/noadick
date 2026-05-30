"""Command-cooldown helper with a one-shot, self-deleting notice.

Game commands are throttled per (chat, user) to stop a user flooding a group by
spamming a command. Previously a throttled command was silently ignored, which
looks broken to the user. ``passes`` keeps the throttle but, on the *first*
rejection in a cooldown window, replies with a short notice that deletes itself
after a few seconds. Further spam within the same window stays silent, so the
notice itself cannot become a flood.
"""
from __future__ import annotations

import asyncio
import contextlib

from aiogram.types import Message

import texts
from services import cooldown

# How long the transient notice lives before deleting itself.
NOTICE_TTL = 15


async def _autodelete(message: Message, delay: int) -> None:
    await asyncio.sleep(delay)
    with contextlib.suppress(Exception):
        await message.delete()


async def passes(message: Message, user_id: int, key: str, seconds: float) -> bool:
    """Return True if the command may run. If it is on cooldown, return False and
    — at most once per cooldown window — reply with a self-deleting notice."""
    chat_id = message.chat.id
    if cooldown.check_and_touch(chat_id, user_id, key, seconds):
        return True
    # Throttled. Notify only once per window using a sibling cooldown of equal
    # length, keyed off the same command so notices can't pile up.
    if cooldown.check_and_touch(chat_id, user_id, f"{key}:cdnotice", seconds):
        with contextlib.suppress(Exception):
            sent = await message.reply(texts.COOLDOWN_NOTICE)
            asyncio.create_task(_autodelete(sent, NOTICE_TTL))
    return False
