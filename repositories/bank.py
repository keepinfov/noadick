from __future__ import annotations

import asyncio
import time

from sqlalchemy import select, update

from db.engine import get_session_factory
from db.models import Corporation, Deposit, Loan

_CORP_ID = 1

# The Corporation is a single global row shared by every chat, but the money ops
# in services.bank are only guarded by per-chat locks. This process-wide lock
# serialises the "read balance -> decide -> apply" critical sections so two chats
# can't both lend out the same cash. Always acquired *after* a chat lock, so the
# ordering is fixed (chat -> corp) and cannot deadlock.
_CORP_LOCK = asyncio.Lock()


def corp_lock() -> asyncio.Lock:
    return _CORP_LOCK


def _now() -> int:
    return int(time.time())


# ---- Corporation (single global house account) ----


async def get_corp() -> Corporation:
    """Return the singleton Corporation row, creating it on first access."""
    factory = get_session_factory()
    async with factory() as session:
        corp = await session.get(Corporation, _CORP_ID)
        if corp is None:
            corp = Corporation(id=_CORP_ID)
            session.add(corp)
            await session.commit()
            await session.refresh(corp)
        return corp


async def corp_apply(
    *,
    delta: int,
    tax: int = 0,
    interest_earned: int = 0,
    interest_paid: int = 0,
    penalties: int = 0,
) -> int:
    """Atomically move the Corporation balance by ``delta`` and bump the matching
    lifetime counters. Returns the new balance. ``delta`` may be negative (the
    house can go into the red — that is the bankruptcy state)."""
    factory = get_session_factory()
    async with factory() as session:
        corp = await session.get(Corporation, _CORP_ID)
        if corp is None:
            corp = Corporation(id=_CORP_ID)
            session.add(corp)
            await session.flush()  # materialise the row + column defaults
        # Atomic in-DB increments: read-modify-write in Python would lose updates
        # under concurrent calls (the corp is shared across all chats).
        await session.execute(
            update(Corporation)
            .where(Corporation.id == _CORP_ID)
            .values(
                balance=Corporation.balance + delta,
                total_tax=Corporation.total_tax + tax,
                total_interest_earned=Corporation.total_interest_earned + interest_earned,
                total_interest_paid=Corporation.total_interest_paid + interest_paid,
                total_penalties=Corporation.total_penalties + penalties,
            )
        )
        await session.commit()
        new_balance = await session.scalar(
            select(Corporation.balance).where(Corporation.id == _CORP_ID)
        )
        return int(new_balance or 0)


async def set_rules_urls(rude: str, strict: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        corp = await session.get(Corporation, _CORP_ID)
        if corp is None:
            corp = Corporation(id=_CORP_ID)
            session.add(corp)
        corp.rules_url_rude = rude
        corp.rules_url_strict = strict
        await session.commit()


# ---- Deposits ----


async def get_deposit(chat_id: int, user_id: int) -> Deposit | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(Deposit, (chat_id, user_id))


async def upsert_deposit(chat_id: int, user_id: int, **fields) -> Deposit:
    factory = get_session_factory()
    async with factory() as session:
        dep = await session.get(Deposit, (chat_id, user_id))
        if dep is None:
            dep = Deposit(chat_id=chat_id, user_id=user_id, opened_at=_now())
            session.add(dep)
        for key, value in fields.items():
            setattr(dep, key, value)
        await session.commit()
        await session.refresh(dep)
        return dep


async def delete_deposit(chat_id: int, user_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        dep = await session.get(Deposit, (chat_id, user_id))
        if dep is not None:
            await session.delete(dep)
            await session.commit()


async def all_deposits() -> list[Deposit]:
    factory = get_session_factory()
    async with factory() as session:
        return list((await session.execute(select(Deposit))).scalars().all())


# ---- Loans ----


async def get_loan(chat_id: int, user_id: int) -> Loan | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(Loan, (chat_id, user_id))


async def upsert_loan(chat_id: int, user_id: int, **fields) -> Loan:
    factory = get_session_factory()
    async with factory() as session:
        loan = await session.get(Loan, (chat_id, user_id))
        if loan is None:
            loan = Loan(chat_id=chat_id, user_id=user_id, opened_at=_now())
            session.add(loan)
        for key, value in fields.items():
            setattr(loan, key, value)
        await session.commit()
        await session.refresh(loan)
        return loan


async def delete_loan(chat_id: int, user_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        loan = await session.get(Loan, (chat_id, user_id))
        if loan is not None:
            await session.delete(loan)
            await session.commit()


async def all_loans() -> list[Loan]:
    factory = get_session_factory()
    async with factory() as session:
        return list((await session.execute(select(Loan))).scalars().all())
