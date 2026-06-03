"""Effective per-chat settings with a small in-memory TTL cache.

A chat may override gameplay knobs (timezone, diseases on/off, default duel
stake/timeout). When unset, process-wide defaults apply (env ``TZ`` and the
historical hardcoded values). Reads are cached for ``_TTL`` seconds to avoid a
DB round-trip on every game command; writes invalidate the chat's entry.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from repositories import chat_settings as repo

_TTL = 300  # seconds

DEFAULT_DISEASES_ENABLED = True
DEFAULT_BANKING_ENABLED = True
DEFAULT_DUEL_STAKE = 5
DEFAULT_DUEL_TIMEOUT = 60

# Bounds for admin-supplied values (also documented to users via /gameconfig).
MIN_DUEL_STAKE = 1
MAX_DUEL_STAKE = 1000
MIN_DUEL_TIMEOUT = 10
MAX_DUEL_TIMEOUT = 600

# User-facing /gameconfig keys.
SETTING_KEYS = ("tz", "diseases", "duel_stake", "duel_timeout")


@dataclass(frozen=True)
class EffectiveSettings:
    tz: str
    diseases_enabled: bool
    banking_enabled: bool
    duel_stake_default: int
    duel_timeout: int


def _env_tz() -> str:
    return os.environ.get("TZ", "Europe/Moscow")


_cache: dict[int, tuple[float, EffectiveSettings]] = {}


def invalidate(chat_id: int) -> None:
    _cache.pop(chat_id, None)


async def get_effective(chat_id: int) -> EffectiveSettings:
    cached = _cache.get(chat_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _TTL:
        return cached[1]

    row = await repo.get_settings(chat_id)
    if row is None:
        eff = EffectiveSettings(
            tz=_env_tz(),
            diseases_enabled=DEFAULT_DISEASES_ENABLED,
            banking_enabled=DEFAULT_BANKING_ENABLED,
            duel_stake_default=DEFAULT_DUEL_STAKE,
            duel_timeout=DEFAULT_DUEL_TIMEOUT,
        )
    else:
        eff = EffectiveSettings(
            tz=row.tz or _env_tz(),
            diseases_enabled=bool(row.diseases_enabled),
            banking_enabled=bool(row.banking_enabled),
            duel_stake_default=int(row.duel_stake_default),
            duel_timeout=int(row.duel_timeout),
        )
    _cache[chat_id] = (now, eff)
    return eff


async def resolve_tz(chat_id: int) -> ZoneInfo:
    eff = await get_effective(chat_id)
    try:
        return ZoneInfo(key=eff.tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(key=_env_tz())


def _parse_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in {"on", "true", "1", "yes", "да", "вкл"}:
        return True
    if v in {"off", "false", "0", "no", "нет", "выкл"}:
        return False
    return None


class SettingError(ValueError):
    """Raised when an admin supplies an unknown key or invalid value."""


async def set_setting(chat_id: int, key: str, raw_value: str) -> None:
    """Validate and persist a single /gameconfig setting; invalidates cache.

    Raises SettingError with a user-facing message on bad key/value.
    """
    key = key.strip().lower()
    if key not in SETTING_KEYS:
        raise SettingError(f"unknown_key:{key}")

    if key == "tz":
        try:
            ZoneInfo(key=raw_value.strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingError("bad_tz") from None
        await repo.upsert_settings(chat_id, tz=raw_value.strip())

    elif key == "diseases":
        parsed = _parse_bool(raw_value)
        if parsed is None:
            raise SettingError("bad_bool")
        await repo.upsert_settings(chat_id, diseases_enabled=parsed)

    elif key == "duel_stake":
        if not raw_value.strip().isdigit():
            raise SettingError("bad_int")
        val = int(raw_value.strip())
        if not (MIN_DUEL_STAKE <= val <= MAX_DUEL_STAKE):
            raise SettingError("out_of_range")
        await repo.upsert_settings(chat_id, duel_stake_default=val)

    elif key == "duel_timeout":
        if not raw_value.strip().isdigit():
            raise SettingError("bad_int")
        val = int(raw_value.strip())
        if not (MIN_DUEL_TIMEOUT <= val <= MAX_DUEL_TIMEOUT):
            raise SettingError("out_of_range")
        await repo.upsert_settings(chat_id, duel_timeout=val)

    invalidate(chat_id)


async def adjust(chat_id: int, key: str, delta: int) -> int:
    """Clamp a numeric per-chat setting by ``delta`` within its bounds, persist,
    and invalidate the cache. Returns the new value. Used by the button panels."""
    eff = await get_effective(chat_id)
    if key == "duel_stake":
        val = max(MIN_DUEL_STAKE, min(MAX_DUEL_STAKE, eff.duel_stake_default + delta))
        await repo.upsert_settings(chat_id, duel_stake_default=val)
    elif key == "duel_timeout":
        val = max(MIN_DUEL_TIMEOUT, min(MAX_DUEL_TIMEOUT, eff.duel_timeout + delta))
        await repo.upsert_settings(chat_id, duel_timeout=val)
    else:
        raise SettingError(f"unknown_key:{key}")
    invalidate(chat_id)
    return val


async def toggle_diseases(chat_id: int) -> bool:
    """Flip the per-chat diseases switch; returns the new state."""
    eff = await get_effective(chat_id)
    new_val = not eff.diseases_enabled
    await repo.upsert_settings(chat_id, diseases_enabled=new_val)
    invalidate(chat_id)
    return new_val


async def toggle_banking(chat_id: int) -> bool:
    """Flip the per-chat banking switch; returns the new state."""
    eff = await get_effective(chat_id)
    new_val = not eff.banking_enabled
    await repo.upsert_settings(chat_id, banking_enabled=new_val)
    invalidate(chat_id)
    return new_val
