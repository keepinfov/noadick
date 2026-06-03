"""One-off: publish the bank rules to Telegraph and store the URLs.

Usage (from repo root):  python -m scripts.publish_rules
"""
from __future__ import annotations

import asyncio

from db.engine import dispose_engine, init_db
from services import rules


async def main() -> None:
    await init_db()
    try:
        rude, strict = await rules.publish()
        print("Rude rules:  ", rude)
        print("Strict rules:", strict)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
