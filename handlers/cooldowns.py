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


def schedule_autodelete(message: Message, delay: int = NOTICE_TTL) -> None:
    """Fire-and-forget: delete ``message`` after ``delay`` seconds. Used for
    transient throttle replies so they don't clutter the chat."""
    asyncio.create_task(_autodelete(message, delay))


async def _send_notice(message: Message) -> Message | None:
    """Best-effort notice send. Reply to the triggering command, but fall back to
    a plain chat message if the reply fails (e.g. the command was deleted, or a
    forum-topic edge case), so the notice still appears."""
    try:
        return await message.reply(texts.COOLDOWN_NOTICE)
    except Exception:
        pass
    with contextlib.suppress(Exception):
        return await message.answer(texts.COOLDOWN_NOTICE)
    return None


async def passes(message: Message, user_id: int, key: str, seconds: float) -> bool:
    """Return True if the command may run. If it is on cooldown, return False and
    — at most once per cooldown window — reply with a self-deleting notice."""
    chat_id = message.chat.id
    if cooldown.check_and_touch(chat_id, user_id, key, seconds):
        return True
    # Throttled. Notify only once per window using a sibling cooldown of equal
    # length, keyed off the same command so notices can't pile up. Only arm the
    # once-per-window flag once a message has actually gone out, so a failed send
    # doesn't silently eat the only notice for the whole window.
    if not cooldown.check_and_touch(chat_id, user_id, f"{key}:cdnotice", seconds):
        return False
    sent = await _send_notice(message)
    if sent is not None:
        asyncio.create_task(_autodelete(sent, NOTICE_TTL))
    else:
        # Send failed — release the flag so the next attempt can retry.
        cooldown.reset(chat_id, user_id, f"{key}:cdnotice")
    return False
