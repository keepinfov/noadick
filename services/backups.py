from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def create_backup(source: Path, destination: Path, retention: int) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    target = destination / f"bot-{stamp}.db"
    temporary = target.with_suffix(".db.tmp")

    with sqlite3.connect(source) as source_db, sqlite3.connect(temporary) as backup_db:
        source_db.backup(backup_db)
        result = backup_db.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("SQLite integrity check failed for the new backup")
    temporary.chmod(0o600)
    os.replace(temporary, target)

    backups = sorted(destination.glob("bot-*.db"), key=lambda path: path.stat().st_mtime)
    for obsolete in backups[:-retention]:
        obsolete.unlink()
    return target


async def backup_loop(source: Path, destination: Path, interval_hours: int, retention: int) -> None:
    if interval_hours <= 0:
        logger.info("Scheduled database backups are disabled")
        return
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            backup = await asyncio.to_thread(create_backup, source, destination, retention)
            logger.info("Database backup completed", extra={"backup": backup.name})
        except (OSError, sqlite3.Error, RuntimeError):
            logger.exception("Database backup failed")


async def heartbeat_loop(path: Path, interval_seconds: int = 15) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        path.touch()
        await asyncio.sleep(interval_seconds)
