from __future__ import annotations

from sqlalchemy import select

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


async def enabled_digests() -> list[ChatSettings]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(ChatSettings).where(ChatSettings.stats_digest_enabled.is_(True))
                )
            )
            .scalars()
            .all()
        )


async def mark_digest_sent(chat_id: int, week_key: str) -> None:
    await upsert_settings(chat_id, stats_digest_last_week=week_key)
