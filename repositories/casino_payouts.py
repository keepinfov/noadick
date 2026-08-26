from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import get_session_factory
from db.models import CasinoPayoutRule


async def get_rule_in(session: AsyncSession, slot_value: int) -> CasinoPayoutRule | None:
    return await session.get(CasinoPayoutRule, slot_value)


async def get_rule(slot_value: int) -> CasinoPayoutRule | None:
    factory = get_session_factory()
    async with factory() as session:
        return await get_rule_in(session, slot_value)


async def count_rules() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return int(
            (await session.execute(select(func.count(CasinoPayoutRule.slot_value)))).scalar_one()
        )


async def list_rules(*, offset: int = 0, limit: int | None = None) -> list[CasinoPayoutRule]:
    factory = get_session_factory()
    async with factory() as session:
        statement = select(CasinoPayoutRule).order_by(CasinoPayoutRule.slot_value)
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return list((await session.execute(statement)).scalars())
