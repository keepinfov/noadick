from __future__ import annotations

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def database_path() -> Path:
    return Path(os.environ.get("DB_PATH", "./data/bot.db")).resolve()


def _upgrade(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.attributes["configure_logger"] = False
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")


async def upgrade_database() -> None:
    await asyncio.to_thread(_upgrade, database_path())
