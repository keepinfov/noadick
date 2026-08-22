from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.engine import get_session_factory
from db.models import Chat, DepositInsurance, Event, WeeklySeason, WeeklySeasonPlayer
from repositories import events as E


@dataclass(frozen=True)
class PlayerSeed:
    user_id: int
    name: str
    length: int
    wealth: int


@dataclass(frozen=True)
class PlayerFinal:
    user_id: int
    name: str
    start_length: int
    end_length: int
    start_wealth: int
    end_wealth: int
    dick_total: int
    dick_count: int
    active_days: int
    best_dick_delta: int | None
    worst_dick_delta: int | None
    wealth_delta: int


@dataclass(frozen=True)
class SeasonFinal:
    is_empty: bool
    length_end: int
    wealth_end: int
    emission: int
    best_dick_delta: int | None
    best_dick_user_id: int | None
    worst_dick_delta: int | None
    worst_dick_user_id: int | None
    players: tuple[PlayerFinal, ...]


def _with_players():
    return selectinload(WeeklySeason.players)


async def get_live_in(session: AsyncSession, chat_id: int) -> WeeklySeason | None:
    return (
        await session.execute(
            select(WeeklySeason)
            .options(_with_players())
            .where(WeeklySeason.chat_id == chat_id, WeeklySeason.status == "live")
        )
    ).scalar_one_or_none()


async def get_live(chat_id: int) -> WeeklySeason | None:
    factory = get_session_factory()
    async with factory() as session:
        return await get_live_in(session, chat_id)


async def get_by_number(chat_id: int, season_number: int) -> WeeklySeason | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(WeeklySeason)
                .options(_with_players())
                .where(
                    WeeklySeason.chat_id == chat_id,
                    WeeklySeason.season_number == season_number,
                )
            )
        ).scalar_one_or_none()


async def list_finalized(chat_id: int, *, offset: int = 0, limit: int = 20) -> list[WeeklySeason]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(WeeklySeason)
                    .options(_with_players())
                    .where(
                        WeeklySeason.chat_id == chat_id,
                        WeeklySeason.status == "finalized",
                        WeeklySeason.is_partial.is_(False),
                    )
                    .order_by(WeeklySeason.season_number.desc())
                    .offset(max(0, offset))
                    .limit(max(1, limit))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def list_pending(chat_id: int) -> list[WeeklySeason]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(WeeklySeason)
                    .options(_with_players())
                    .where(
                        WeeklySeason.chat_id == chat_id,
                        WeeklySeason.status == "finalized",
                        WeeklySeason.is_empty.is_(False),
                        WeeklySeason.publication_status == "pending",
                    )
                    .order_by(WeeklySeason.season_number.desc())
                )
            )
            .scalars()
            .all()
        )


async def mark_skipped(chat_id: int, season_numbers: Iterable[int]) -> int:
    numbers = tuple(season_numbers)
    if not numbers:
        return 0
    factory = get_session_factory()
    async with factory() as session, session.begin():
        rows = list(
            (
                await session.execute(
                    select(WeeklySeason).where(
                        WeeklySeason.chat_id == chat_id,
                        WeeklySeason.season_number.in_(numbers),
                        WeeklySeason.status == "finalized",
                        WeeklySeason.publication_status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.publication_status = "skipped"
        return len(rows)


async def next_number_in(session: AsyncSession, chat_id: int) -> int:
    value = await session.scalar(
        select(func.coalesce(func.max(WeeklySeason.season_number), 0)).where(
            WeeklySeason.chat_id == chat_id,
            WeeklySeason.is_partial.is_(False),
        )
    )
    return int(value or 0) + 1


async def create_live_in(
    session: AsyncSession,
    *,
    chat_id: int,
    season_number: int,
    timezone: str,
    starts_at: int,
    ends_at: int,
    tracking_since: int,
    is_partial: bool,
    players: Iterable[PlayerSeed],
    created_at: int,
) -> WeeklySeason:
    if await session.get(Chat, chat_id) is None:
        session.add(Chat(chat_id=chat_id))
        await session.flush()
    seeds = tuple(players)
    season = WeeklySeason(
        chat_id=chat_id,
        season_number=season_number,
        timezone=timezone,
        starts_at=starts_at,
        ends_at=ends_at,
        tracking_since=tracking_since,
        status="live",
        is_partial=is_partial,
        is_empty=True,
        publication_status="skipped" if is_partial else "pending",
        length_start=sum(seed.length for seed in seeds),
        length_end=sum(seed.length for seed in seeds),
        wealth_start=sum(seed.wealth for seed in seeds),
        wealth_end=sum(seed.wealth for seed in seeds),
        created_at=created_at,
        players=[
            WeeklySeasonPlayer(
                user_id=seed.user_id,
                name=seed.name,
                start_length=seed.length,
                end_length=seed.length,
                start_wealth=seed.wealth,
                end_wealth=seed.wealth,
            )
            for seed in seeds
        ],
    )
    session.add(season)
    await session.flush()
    return season


async def discard_partial_in(session: AsyncSession, season: WeeklySeason) -> None:
    if not season.is_partial or season.status != "live":
        raise ValueError("only a live partial season can be discarded")
    await session.delete(season)
    await session.flush()


async def finalize_in(
    session: AsyncSession,
    season: WeeklySeason,
    result: SeasonFinal,
    *,
    finalized_at: int,
    sekasko_prize_ranks: Mapping[int, int] | None = None,
    sekasko_prize_amount: int = 0,
    sekasko_max_coverage: int = 0,
    sekasko_prize_expires_at: int = 0,
) -> WeeklySeason:
    if season.status != "live" or season.is_partial:
        raise ValueError("only a live official season can be finalized")
    season.status = "finalized"
    season.is_empty = result.is_empty
    season.publication_status = "skipped" if result.is_empty else "pending"
    season.length_end = result.length_end
    season.wealth_end = result.wealth_end
    season.emission = result.emission
    season.best_dick_delta = result.best_dick_delta
    season.best_dick_user_id = result.best_dick_user_id
    season.worst_dick_delta = result.worst_dick_delta
    season.worst_dick_user_id = result.worst_dick_user_id
    season.finalized_at = finalized_at

    prize_ranks = sekasko_prize_ranks or {}
    prizes_enabled = (
        not result.is_empty
        and sekasko_prize_amount > 0
        and sekasko_max_coverage > 0
        and sekasko_prize_expires_at > finalized_at
    )
    existing = {row.user_id: row for row in season.players}
    final_ids: set[int] = set()
    for value in result.players:
        final_ids.add(value.user_id)
        row = existing.get(value.user_id)
        if row is None:
            row = WeeklySeasonPlayer(season_id=season.id, user_id=value.user_id)
            session.add(row)
        row.name = value.name
        row.start_length = value.start_length
        row.end_length = value.end_length
        row.start_wealth = value.start_wealth
        row.end_wealth = value.end_wealth
        row.dick_total = value.dick_total
        row.dick_count = value.dick_count
        row.active_days = value.active_days
        row.best_dick_delta = value.best_dick_delta
        row.worst_dick_delta = value.worst_dick_delta
        row.wealth_delta = value.wealth_delta
        prize_rank = int(prize_ranks.get(value.user_id, 0))
        eligible = prizes_enabled and prize_rank > 0 and value.dick_count > 0
        active_coverage = 0
        if eligible:
            active_coverage = int(
                await session.scalar(
                    select(func.coalesce(func.sum(DepositInsurance.amount), 0)).where(
                        DepositInsurance.chat_id == season.chat_id,
                        DepositInsurance.user_id == value.user_id,
                        DepositInsurance.expires_at > finalized_at,
                    )
                )
                or 0
            )
        awarded = (
            min(
                sekasko_prize_amount,
                max(0, sekasko_max_coverage - max(0, active_coverage)),
            )
            if eligible
            else 0
        )
        if awarded > 0:
            row.sekasko_prize_rank = prize_rank
            row.sekasko_prize_amount = awarded
            row.sekasko_prize_expires_at = sekasko_prize_expires_at
            session.add(
                DepositInsurance(
                    chat_id=season.chat_id,
                    user_id=value.user_id,
                    amount=awarded,
                    premium=0,
                    expires_at=sekasko_prize_expires_at,
                    source="season_prize",
                    source_season_id=season.id,
                    created_at=finalized_at,
                )
            )
            session.add(
                Event(
                    chat_id=season.chat_id,
                    user_id=value.user_id,
                    type=E.SEASON_SEKASKO_PRIZE,
                    delta=0,
                    size_after=value.end_length,
                    meta={
                        "season_number": season.season_number,
                        "rank": prize_rank,
                        "coverage": awarded,
                        "expires_at": sekasko_prize_expires_at,
                    },
                    created_at=finalized_at,
                )
            )
        else:
            row.sekasko_prize_rank = 0
            row.sekasko_prize_amount = 0
            row.sekasko_prize_expires_at = 0
    for user_id, row in existing.items():
        if user_id not in final_ids:
            await session.delete(row)
    await session.flush()
    return season


async def mark_published(
    chat_id: int,
    season_number: int,
    *,
    message_id: int,
    published_at: int | None = None,
) -> bool:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        season = (
            await session.execute(
                select(WeeklySeason).where(
                    WeeklySeason.chat_id == chat_id,
                    WeeklySeason.season_number == season_number,
                    WeeklySeason.status == "finalized",
                )
            )
        ).scalar_one_or_none()
        if season is None or season.publication_status == "published" or season.is_empty:
            return False
        season.publication_status = "published"
        season.published_at = published_at if published_at is not None else int(time.time())
        season.published_message_id = message_id
        return True
