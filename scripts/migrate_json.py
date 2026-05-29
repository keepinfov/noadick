"""Import legacy storage/*.json files into the legacy_chats staging table.

Each JSON file is named md5(chat_id).json, so the real chat_id is unknown here.
The data is staged by its md5 hash; the bot relinks it to the real chat_id on
the first message from that chat (see services/registry.relink_legacy).

Usage:
    python -m scripts.migrate_json [STORAGE_DIR]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from db.engine import get_session_factory, init_db
from db.models import LegacyChat


async def main() -> None:
    storage_dir = Path(
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STORAGE_PATH", "./storage")
    )
    await init_db()

    if not storage_dir.exists():
        print(f"Storage dir not found: {storage_dir}")
        return

    factory = get_session_factory()
    imported = 0
    skipped = 0
    async with factory() as session:
        for path in sorted(storage_dir.glob("*.json")):
            h = path.stem
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                print(f"  skip (invalid json): {path.name}")
                continue
            existing = await session.get(LegacyChat, h)
            if existing is not None:
                skipped += 1
                continue
            session.add(LegacyChat(hash=h, data=data))
            imported += 1
            print(f"  staged: {path.name} ({len(data)} players)")
        await session.commit()

    print(f"Done. Staged {imported} files, skipped {skipped} already-present.")


if __name__ == "__main__":
    asyncio.run(main())
