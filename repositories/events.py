from __future__ import annotations

import time

from sqlalchemy import case, func, select

from db.engine import get_session_factory
from db.models import Event

# Event type constants.
DICK = "dick"
DUEL = "duel"
INFECTION = "infection"
ADMIN_ADJUST = "admin_adjust"
BASELINE = "baseline"
# Banking events.
DEPOSIT_OPEN = "deposit_open"
DEPOSIT_WITHDRAW = "deposit_withdraw"
DEPOSIT_INTEREST = "deposit_interest"
DEPOSIT_PENALTY = "deposit_penalty"
CONFISCATION = "confiscation"
LOAN_OPEN = "loan_open"
LOAN_REPAY = "loan_repay"
LOAN_INTEREST = "loan_interest"
LOAN_GARNISH = "loan_garnish"
LOAN_DEFAULT = "loan_default"
CORP_TAX = "corp_tax"


async def log_event(
    chat_id: int,
    user_id: int,
    etype: str,
    *,
    delta: int = 0,
    size_after: int = 0,
    meta: dict | None = None,
    created_at: int | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            Event(
                chat_id=chat_id,
                user_id=user_id,
                type=etype,
                delta=delta,
                size_after=size_after,
                meta=meta,
                created_at=created_at if created_at is not None else int(time.time()),
            )
        )
        await session.commit()


async def has_events(chat_id: int, user_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        found = (
            await session.execute(
                select(Event.id)
                .where(Event.chat_id == chat_id, Event.user_id == user_id)
                .limit(1)
            )
        ).first()
        return found is not None


async def ensure_baseline(
    chat_id: int, user_id: int, size_before: int, created_at: int | None = None
) -> None:
    """Write a baseline event capturing the player's size *before* their first
    logged event, so the size timeline has a sensible starting point. No-op if
    the player already has events or had no size to record."""
    if size_before == 0:
        return
    if await has_events(chat_id, user_id):
        return
    ts = (created_at if created_at is not None else int(time.time())) - 1
    await log_event(
        chat_id, user_id, BASELINE, delta=0, size_after=size_before, created_at=ts
    )


async def get_events(
    chat_id: int,
    user_id: int,
    types: list[str] | None = None,
    since: int | None = None,
    limit: int | None = None,
) -> list[Event]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Event).where(
            Event.chat_id == chat_id, Event.user_id == user_id
        )
        if types:
            stmt = stmt.where(Event.type.in_(types))
        if since is not None:
            stmt = stmt.where(Event.created_at >= since)
        stmt = stmt.order_by(Event.created_at)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await session.execute(stmt)).scalars().all())


async def count_by_type(chat_id: int, user_id: int) -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Event.type, func.count(Event.id))
                .where(Event.chat_id == chat_id, Event.user_id == user_id)
                .group_by(Event.type)
            )
        ).all()
        return {etype: cnt for etype, cnt in rows}


# ---- cross-chat aggregation (global profile) ----


async def global_dick_aggregate(
    user_id: int,
) -> tuple[int, int, int, int | None, int | None]:
    """Aggregate /dick events across all chats for a user.
    Returns (plays, total_grown, total_lost, best_roll, worst_roll)."""
    factory = get_session_factory()
    async with factory() as session:
        plays, grown, lost, best, worst = (
            await session.execute(
                select(
                    func.count(Event.id),
                    func.coalesce(
                        func.sum(case((Event.delta > 0, Event.delta), else_=0)), 0
                    ),
                    func.coalesce(
                        func.sum(case((Event.delta < 0, -Event.delta), else_=0)), 0
                    ),
                    func.max(Event.delta),
                    func.min(Event.delta),
                ).where(Event.user_id == user_id, Event.type == DICK)
            )
        ).one()
        return plays, grown, lost, best, worst


async def global_count_by_type(user_id: int) -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Event.type, func.count(Event.id))
                .where(Event.user_id == user_id)
                .group_by(Event.type)
            )
        ).all()
        return {etype: cnt for etype, cnt in rows}


async def global_best_size(user_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(func.coalesce(func.max(Event.size_after), 0))
                .where(Event.user_id == user_id)
            )
        ).scalar_one()


async def global_first_play(user_id: int) -> int | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(func.min(Event.created_at))
                .where(Event.user_id == user_id, Event.type != BASELINE)
            )
        ).scalar_one()


async def global_duel_events(user_id: int) -> list[Event]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(Event).where(
                        Event.user_id == user_id, Event.type == DUEL
                    )
                )
            ).scalars().all()
        )
