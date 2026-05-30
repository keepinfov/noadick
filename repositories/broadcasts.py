from __future__ import annotations

from sqlalchemy import func, select

from db.engine import get_session_factory
from db.models import BroadcastLog


async def insert_broadcast(
    admin_id: int, preview: str, target_mode: str, sent: int, failed: int
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            BroadcastLog(
                admin_id=admin_id,
                preview=preview,
                target_mode=target_mode,
                sent=sent,
                failed=failed,
            )
        )
        await session.commit()


async def list_broadcasts(offset: int = 0, limit: int = 10) -> list[BroadcastLog]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(BroadcastLog)
                .order_by(BroadcastLog.created_at.desc(), BroadcastLog.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)


async def count_broadcasts() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(select(func.count(BroadcastLog.id)))
        ).scalar_one()
