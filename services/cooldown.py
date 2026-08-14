"""In-memory per-(chat, user, key) cooldowns.

Used to throttle command replies so a user cannot make the bot flood a group by
repeating a command. Memory-only (``time.monotonic``); cleared on restart, which
is fine — cooldowns are short-lived anti-spam, not durable state.
"""

from __future__ import annotations

import time

_last: dict[tuple[int, int, str], tuple[float, float]] = {}
_touches = 0


def _prune(now: float) -> None:
    global _touches
    _touches += 1
    if _touches % 256:
        return
    expired = [key for key, (last, ttl) in _last.items() if now - last >= ttl]
    for key in expired:
        _last.pop(key, None)


def check_and_touch(chat_id: int, user_id: int, key: str, seconds: float) -> bool:
    """Return True if the action is allowed (and record it), False if the caller
    is still within the cooldown window for this (chat, user, key)."""
    now = time.monotonic()
    _prune(now)
    k = (chat_id, user_id, key)
    entry = _last.get(k)
    if entry is not None and now - entry[0] < seconds:
        return False
    _last[k] = (now, seconds)
    return True


def peek(chat_id: int, user_id: int, key: str, seconds: float) -> bool:
    """Return True if the action is allowed, WITHOUT recording a touch. Use when
    the touch should happen conditionally (e.g. only on a rejection)."""
    entry = _last.get((chat_id, user_id, key))
    return entry is None or (time.monotonic() - entry[0]) >= seconds


def touch(chat_id: int, user_id: int, key: str) -> None:
    """Record a touch now, opening a fresh cooldown window for this key."""
    _last[(chat_id, user_id, key)] = (time.monotonic(), 3600)


def reset(chat_id: int, user_id: int, key: str) -> None:
    """Forget a recorded touch so the next check_and_touch is allowed again.
    Used to release a once-per-window flag whose action failed."""
    _last.pop((chat_id, user_id, key), None)
