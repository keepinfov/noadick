"""Process-wide tunables with a single in-memory snapshot.

These knobs (command cooldowns, duel params, broadcast pacing, list page size,
active-chat window) used to be hardcoded constants. They are now stored in a
single ``global_settings`` row and edited from the admin panel.

Hot paths (the per-message cooldown checks and the duel chance calc) must not do
a DB round-trip, so reads go through :func:`get_config_sync`, which returns a
process-global snapshot (or the hardcoded defaults before it is populated). The
snapshot is refreshed at startup (``bot.py`` after ``init_db``) and eagerly after
every write, so it is always current without polling.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

from repositories import global_settings as repo

# key -> (human label, small step, big step, min, max). Drives both the panel
# keyboard and the clamp bounds, so they can never drift apart.
EDITABLE: list[tuple[str, str, int, int, int, int]] = [
    ("cd_duel", "КД /duel (сек)", 5, 30, 0, 3600),
    ("cd_top", "КД /top (сек)", 5, 30, 0, 3600),
    ("cd_me", "КД /me (сек)", 5, 30, 0, 3600),
    ("cd_ping", "КД /ping (сек)", 5, 30, 0, 3600),
    ("cd_help", "КД /help (сек)", 5, 30, 0, 3600),
    ("cd_chat_ban_notice", "КД увед. локал-бана (сек)", 30, 300, 0, 86400),
    ("cd_ban_notice", "КД увед. бана (сек)", 30, 300, 0, 86400),
    ("cd_dm_gate", "КД DM-гейта (сек)", 30, 300, 0, 86400),
    ("max_pending_duels", "Макс. дуэлей в очереди", 1, 5, 1, 50),
    ("size_weight_pct", "Вес размера в дуэли (%)", 5, 10, 0, 100),
    ("bcast_rate_delay_ms", "Пауза рассылки (мс)", 10, 50, 0, 5000),
    ("active_days", "Окно «активных» (дней)", 5, 30, 1, 365),
    ("page_size", "Размер страницы", 1, 5, 3, 50),
]
BOUNDS: dict[str, tuple[int, int]] = {k: (mn, mx) for k, _, _, _, mn, mx in EDITABLE}
LABELS: dict[str, str] = {k: lbl for k, lbl, _, _, _, _ in EDITABLE}

DEFAULTS: dict[str, int] = {
    "cd_duel": 10,
    "cd_top": 5,
    "cd_me": 5,
    "cd_ping": 5,
    "cd_help": 5,
    "cd_chat_ban_notice": 300,
    "cd_ban_notice": 300,
    "cd_dm_gate": 300,
    "max_pending_duels": 3,
    "size_weight_pct": 20,
    "bcast_rate_delay_ms": 50,
    "active_days": 30,
    "page_size": 8,
}


@dataclass(frozen=True)
class GlobalConfig:
    cd_duel: int
    cd_top: int
    cd_me: int
    cd_ping: int
    cd_help: int
    cd_chat_ban_notice: int
    cd_ban_notice: int
    cd_dm_gate: int
    max_pending_duels: int
    size_weight_pct: int
    bcast_rate_delay_ms: int
    active_days: int
    page_size: int

    @property
    def size_weight(self) -> float:
        return self.size_weight_pct / 100

    @property
    def bcast_rate_delay(self) -> float:
        return self.bcast_rate_delay_ms / 1000


_FIELDS = tuple(f.name for f in fields(GlobalConfig))
_cache: GlobalConfig | None = None


def _defaults() -> GlobalConfig:
    return GlobalConfig(**DEFAULTS)


def get_config_sync() -> GlobalConfig:
    """Return the cached snapshot without touching the DB (safe in hot paths)."""
    return _cache if _cache is not None else _defaults()


async def refresh() -> GlobalConfig:
    """Reload from the DB and repopulate the snapshot."""
    global _cache
    row = await repo.get_row()
    if row is None:
        cfg = _defaults()
    else:
        cfg = GlobalConfig(**{k: getattr(row, k) for k in _FIELDS})
    _cache = cfg
    return cfg


async def get_config() -> GlobalConfig:
    if _cache is not None:
        return _cache
    return await refresh()


def invalidate() -> None:
    global _cache
    _cache = None


async def adjust(key: str, delta: int) -> int:
    """Clamp ``key`` by ``delta`` within BOUNDS, persist, and refresh the
    snapshot. Returns the new value. Raises KeyError for an unknown key."""
    if key not in BOUNDS:
        raise KeyError(key)
    cfg = await get_config()
    mn, mx = BOUNDS[key]
    new_val = max(mn, min(mx, getattr(cfg, key) + delta))
    await repo.upsert(**{key: new_val})
    await refresh()
    return new_val
