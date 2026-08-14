from __future__ import annotations

import asyncio
import time
import weakref

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import get_session_factory
from db.models import (
    Chat,
    ChatCorporation,
    Corporation,
    CorporationLedger,
    Deposit,
    DepositInsurance,
    Loan,
)

_CORP_ID = 1

# Each chat has an independent lock for its local bank balance. It is always
# acquired after the player/chat lock, preserving the fixed chat -> corp order.
_CORP_LOCKS: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


def corp_lock(chat_id: int = 0) -> asyncio.Lock:
    lock = _CORP_LOCKS.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _CORP_LOCKS[chat_id] = lock
    return lock


def _now() -> int:
    return int(time.time())


async def ensure_corp_in(session: AsyncSession, chat_id: int) -> ChatCorporation:
    corp = await session.get(ChatCorporation, chat_id)
    if corp is None:
        if await session.get(Chat, chat_id) is None:
            session.add(Chat(chat_id=chat_id))
            await session.flush()
        corp = ChatCorporation(chat_id=chat_id)
        session.add(corp)
        await session.flush()
    return corp


async def corp_apply_in(
    session: AsyncSession,
    chat_id: int,
    *,
    delta: int,
    reason: str = "manual_adjustment",
    user_id: int = 0,
    meta: dict | None = None,
    tax: int = 0,
    interest_earned: int = 0,
    interest_paid: int = 0,
    penalties: int = 0,
    insurance_delta: int = 0,
    emission: int = 0,
    bailin: int = 0,
    poker_rake: int = 0,
) -> int:
    """Apply a house movement inside the caller-owned transaction."""
    await ensure_corp_in(session, chat_id)
    await session.execute(
        update(ChatCorporation)
        .where(ChatCorporation.chat_id == chat_id)
        .values(
            balance=ChatCorporation.balance + delta,
            total_tax=ChatCorporation.total_tax + tax,
            total_interest_earned=ChatCorporation.total_interest_earned + interest_earned,
            total_interest_paid=ChatCorporation.total_interest_paid + interest_paid,
            total_penalties=ChatCorporation.total_penalties + penalties,
            insurance_reserve=ChatCorporation.insurance_reserve + insurance_delta,
            total_emission=ChatCorporation.total_emission + emission,
            total_bailin=ChatCorporation.total_bailin + bailin,
            total_poker_rake=ChatCorporation.total_poker_rake + poker_rake,
        )
    )
    balance, reserve = (
        await session.execute(
            select(ChatCorporation.balance, ChatCorporation.insurance_reserve).where(
                ChatCorporation.chat_id == chat_id
            )
        )
    ).one()
    session.add(
        CorporationLedger(
            chat_id=chat_id,
            user_id=user_id,
            reason=reason[:32],
            cash_delta=delta,
            reserve_delta=insurance_delta,
            balance_after=int(balance),
            reserve_after=int(reserve),
            meta=meta,
        )
    )
    return int(balance)


async def deposit_liability_in(session: AsyncSession, chat_id: int) -> int:
    value = await session.scalar(
        select(func.coalesce(func.sum(Deposit.principal + Deposit.accrued), 0)).where(
            Deposit.chat_id == chat_id
        )
    )
    return int(value or 0)


async def upsert_deposit_in(session: AsyncSession, chat_id: int, user_id: int, **fields) -> Deposit:
    dep = await session.get(Deposit, (chat_id, user_id))
    if dep is None:
        dep = Deposit(chat_id=chat_id, user_id=user_id, opened_at=_now())
        session.add(dep)
    for key, value in fields.items():
        setattr(dep, key, value)
    await session.flush()
    return dep


async def upsert_loan_in(session: AsyncSession, chat_id: int, user_id: int, **fields) -> Loan:
    loan = await session.get(Loan, (chat_id, user_id))
    if loan is None:
        loan = Loan(chat_id=chat_id, user_id=user_id, opened_at=_now())
        session.add(loan)
    for key, value in fields.items():
        setattr(loan, key, value)
    await session.flush()
    return loan


# ---- Corporation (one operating account per chat) ----


async def get_corp(chat_id: int) -> ChatCorporation:
    """Return a chat-local Corporation row, creating an empty one on demand."""
    factory = get_session_factory()
    async with factory() as session:
        corp = await session.get(ChatCorporation, chat_id)
        if corp is None:
            if await session.get(Chat, chat_id) is None:
                session.add(Chat(chat_id=chat_id))
                await session.flush()
            corp = ChatCorporation(chat_id=chat_id)
            session.add(corp)
            await session.commit()
            await session.refresh(corp)
        return corp


async def corp_apply(
    chat_id: int,
    *,
    delta: int,
    reason: str = "manual_adjustment",
    user_id: int = 0,
    meta: dict | None = None,
    tax: int = 0,
    interest_earned: int = 0,
    interest_paid: int = 0,
    penalties: int = 0,
    insurance_delta: int = 0,
    emission: int = 0,
    bailin: int = 0,
    poker_rake: int = 0,
) -> int:
    """Atomically move the Corporation balance by ``delta`` and bump the matching
    lifetime counters. Returns the new balance. ``delta`` may be negative (the
    house can go into the red — that is the bankruptcy state)."""
    factory = get_session_factory()
    async with factory() as session:
        balance = await corp_apply_in(
            session,
            chat_id,
            delta=delta,
            reason=reason,
            user_id=user_id,
            meta=meta,
            tax=tax,
            interest_earned=interest_earned,
            interest_paid=interest_paid,
            penalties=penalties,
            insurance_delta=insurance_delta,
            emission=emission,
            bailin=bailin,
            poker_rake=poker_rake,
        )
        await session.commit()
        return balance


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


async def get_rules_corp() -> Corporation:
    factory = get_session_factory()
    async with factory() as session:
        corp = await session.get(Corporation, _CORP_ID)
        if corp is None:
            corp = Corporation(id=_CORP_ID)
            session.add(corp)
            await session.commit()
            await session.refresh(corp)
        return corp


async def set_corp_fields(chat_id: int, **fields) -> ChatCorporation:
    factory = get_session_factory()
    async with factory() as session:
        corp = await session.get(ChatCorporation, chat_id)
        if corp is None:
            if await session.get(Chat, chat_id) is None:
                session.add(Chat(chat_id=chat_id))
                await session.flush()
            corp = ChatCorporation(chat_id=chat_id)
            session.add(corp)
        for key, value in fields.items():
            setattr(corp, key, value)
        await session.commit()
        await session.refresh(corp)
        return corp


async def all_corps() -> list[ChatCorporation]:
    factory = get_session_factory()
    async with factory() as session:
        return list((await session.execute(select(ChatCorporation))).scalars().all())


async def deposit_liability(chat_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        value = await session.scalar(
            select(func.coalesce(func.sum(Deposit.principal + Deposit.accrued), 0)).where(
                Deposit.chat_id == chat_id
            )
        )
        return int(value or 0)


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


async def chat_deposits(chat_id: int) -> list[Deposit]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (await session.execute(select(Deposit).where(Deposit.chat_id == chat_id)))
            .scalars()
            .all()
        )


async def active_insurance(chat_id: int, user_id: int, now: int) -> list[DepositInsurance]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(DepositInsurance).where(
                        DepositInsurance.chat_id == chat_id,
                        DepositInsurance.user_id == user_id,
                        DepositInsurance.expires_at > now,
                    )
                )
            )
            .scalars()
            .all()
        )


async def insured_amounts(chat_id: int, user_ids: list[int], now: int) -> dict[int, int]:
    if not user_ids:
        return {}
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    DepositInsurance.user_id,
                    func.coalesce(func.sum(DepositInsurance.amount), 0),
                )
                .where(
                    DepositInsurance.chat_id == chat_id,
                    DepositInsurance.user_id.in_(user_ids),
                    DepositInsurance.expires_at > now,
                )
                .group_by(DepositInsurance.user_id)
            )
        ).all()
    return {int(user_id): int(amount) for user_id, amount in rows}


async def add_insurance(
    chat_id: int, user_id: int, amount: int, premium: int, expires_at: int
) -> DepositInsurance:
    factory = get_session_factory()
    async with factory() as session:
        policy = DepositInsurance(
            chat_id=chat_id,
            user_id=user_id,
            amount=amount,
            premium=premium,
            expires_at=expires_at,
        )
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
        return policy


async def delete_insurance_for_deposit(chat_id: int, user_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            delete(DepositInsurance).where(
                DepositInsurance.chat_id == chat_id, DepositInsurance.user_id == user_id
            )
        )
        await session.commit()


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


async def chat_loans(chat_id: int) -> list[Loan]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (await session.execute(select(Loan).where(Loan.chat_id == chat_id))).scalars().all()
        )
