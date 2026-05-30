from __future__ import annotations

from db.engine import get_session_factory
from db.models import ChatSettings


async def get_settings(chat_id: int) -> ChatSettings | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(ChatSettings, chat_id)


async def upsert_settings(chat_id: int, **fields) -> ChatSettings:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(ChatSettings, chat_id)
        if row is None:
            row = ChatSettings(chat_id=chat_id)
            session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        return row
