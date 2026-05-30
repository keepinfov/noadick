from __future__ import annotations

import os
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


async def dispose_engine() -> None:
    """Close the engine's connection pool on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
