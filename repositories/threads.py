from __future__ import annotations

from sqlalchemy import select

from db.engine import get_session_factory
from db.models import ChatThreadStat


async def bump_thread(chat_id: int, thread_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(ChatThreadStat, (chat_id, thread_id))
        if row is None:
            session.add(ChatThreadStat(chat_id=chat_id, thread_id=thread_id, count=1))
        else:
            row.count += 1
        await session.commit()


async def set_default_thread(chat_id: int, thread_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(ChatThreadStat).where(ChatThreadStat.chat_id == chat_id)
            )
        ).scalars().all()
        target = None
        for r in rows:
            if r.thread_id == thread_id:
                target = r
                r.is_default = True
            else:
                r.is_default = False
        if target is None:
            session.add(
                ChatThreadStat(
                    chat_id=chat_id, thread_id=thread_id, count=0, is_default=True
                )
            )
        await session.commit()


async def clear_default_thread(chat_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(ChatThreadStat).where(
                    ChatThreadStat.chat_id == chat_id,
                    ChatThreadStat.is_default.is_(True),
                )
            )
        ).scalars().all()
        for r in rows:
            r.is_default = False
        await session.commit()
        return bool(rows)


async def resolve_thread(chat_id: int) -> tuple[int | None, str]:
    factory = get_session_factory()
    async with factory() as session:
        explicit = (
            await session.execute(
                select(ChatThreadStat.thread_id)
                .where(
                    ChatThreadStat.chat_id == chat_id,
                    ChatThreadStat.is_default.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if explicit is not None:
            return explicit, "explicit"
        top = (
            await session.execute(
                select(ChatThreadStat.thread_id)
                .where(
                    ChatThreadStat.chat_id == chat_id,
                    ChatThreadStat.count > 0,
                )
                .order_by(ChatThreadStat.count.desc(), ChatThreadStat.thread_id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if top is not None:
            return int(top), "auto"
        return None, "none"
