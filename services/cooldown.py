"""In-memory per-(chat, user, key) cooldowns.

Used to throttle command replies so a user cannot make the bot flood a group by
repeating a command. Memory-only (``time.monotonic``); cleared on restart, which
is fine — cooldowns are short-lived anti-spam, not durable state.
"""
from __future__ import annotations

import time

_last: dict[tuple[int, int, str], float] = {}


def check_and_touch(chat_id: int, user_id: int, key: str, seconds: float) -> bool:
    """Return True if the action is allowed (and record it), False if the caller
    is still within the cooldown window for this (chat, user, key)."""
    now = time.monotonic()
    k = (chat_id, user_id, key)
    last = _last.get(k)
    if last is not None and now - last < seconds:
        return False
    _last[k] = now
    return True


def reset(chat_id: int, user_id: int, key: str) -> None:
    """Forget a recorded touch so the next check_and_touch is allowed again.
    Used to release a once-per-window flag whose action failed."""
    _last.pop((chat_id, user_id, key), None)
