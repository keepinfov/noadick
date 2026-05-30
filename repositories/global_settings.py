from __future__ import annotations

from db.engine import get_session_factory
from db.models import GlobalSettings

_ROW_ID = 1


async def get_row() -> GlobalSettings | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(GlobalSettings, _ROW_ID)


async def upsert(**fields) -> GlobalSettings:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(GlobalSettings, _ROW_ID)
        if row is None:
            row = GlobalSettings(id=_ROW_ID)
            session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        return row
