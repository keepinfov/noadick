from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.migrations import upgrade_database

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

        @event.listens_for(_engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(_engine.sync_engine, "begin")
        def _defer_sqlite_foreign_keys(connection) -> None:
            # ORM flush order does not require relationship objects everywhere;
            # validate cross-row references at commit after all rows exist.
            connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def init_db() -> None:
    await upgrade_database()
    get_engine()


async def dispose_engine() -> None:
    """Close the engine's connection pool on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
