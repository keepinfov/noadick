from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base

# Columns added after the initial schema. create_all() does not ALTER existing
# tables, so these are added idempotently on startup for older databases.
_MIGRATIONS: dict[str, dict[str, str]] = {
    "users": {
        "banned_at": "INTEGER",
        "ban_until": "INTEGER",
    },
    "players": {
        "is_chat_banned": "INTEGER",
        "loans_repaid": "INTEGER DEFAULT 0",
        "loans_defaulted": "INTEGER DEFAULT 0",
    },
    "chat_settings": {
        "banking_enabled": "INTEGER DEFAULT 1",
    },
    "corporation": {
        "deposits_reconciled": "INTEGER DEFAULT 0",
    },
    "global_settings": {
        "dep_rate_pct": "INTEGER DEFAULT 3",
        "dep_rate_decay_pct": "INTEGER DEFAULT 15",
        "dep_rate_floor_pct": "INTEGER DEFAULT 1",
        "dep_yield_cap_pct": "INTEGER DEFAULT 50",
        "dep_term_days": "INTEGER DEFAULT 7",
        "dep_early_penalty_pct": "INTEGER DEFAULT 30",
        "dep_confisc_chance_pct": "INTEGER DEFAULT 2",
        "dep_confisc_max_pct": "INTEGER DEFAULT 10",
        "loan_rate_pct": "INTEGER DEFAULT 5",
        "loan_max_base_pct": "INTEGER DEFAULT 100",
        "loan_min": "INTEGER DEFAULT 15",
        "loan_term_days": "INTEGER DEFAULT 5",
        "loan_garnish_pct": "INTEGER DEFAULT 50",
        "loan_deny_cooldown_sec": "INTEGER DEFAULT 1800",
        "loan_duel_garnish_pct": "INTEGER DEFAULT 50",
        "collector_interval_sec": "INTEGER DEFAULT 3600",
        "reminder_cooldown_sec": "INTEGER DEFAULT 21600",
    },
}

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _db_url() -> str:
    path = os.environ.get("DB_PATH", "./data/bot.db")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{p}"


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_db_url(), echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, columns in _MIGRATIONS.items():
            rows = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in rows}
            for col, col_type in columns.items():
                if col not in existing:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                    )
        await _reconcile_deposits(conn)


async def _reconcile_deposits(conn) -> None:
    """One-off: deposits now fund the Corporation's till, but older databases only
    credited deposit principal to the per-player rows. Back-credit the live
    principal into the house balance exactly once (guarded by a flag)."""
    done = (
        await conn.execute(text("SELECT deposits_reconciled FROM corporation WHERE id = 1"))
    ).scalar()
    if done:
        return
    total = (
        await conn.execute(
            text("SELECT COALESCE(SUM(principal), 0) FROM deposits WHERE principal > 0")
        )
    ).scalar() or 0
    exists = (await conn.execute(text("SELECT 1 FROM corporation WHERE id = 1"))).scalar()
    if exists is None:
        # Seed every column (Python-side ORM defaults don't apply to raw INSERT),
        # so later ORM reads never hit a NULL.
        await conn.execute(
            text(
                "INSERT INTO corporation (id, balance, total_tax, "
                "total_interest_earned, total_interest_paid, total_penalties, "
                "rules_url_rude, rules_url_strict, updated_at, deposits_reconciled) "
                "VALUES (1, :b, 0, 0, 0, 0, '', '', :ts, 1)"
            ),
            {"b": total, "ts": int(time.time())},
        )
    else:
        await conn.execute(
            text(
                "UPDATE corporation SET balance = balance + :b, "
                "deposits_reconciled = 1 WHERE id = 1"
            ),
            {"b": total},
        )


async def dispose_engine() -> None:
    """Close the engine's connection pool on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
