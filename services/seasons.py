"""Calendar-week season calculation without Telegram presentation concerns."""

from __future__ import annotations

import asyncio
import time
import weakref
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from sqlalchemy import select

from db.engine import get_session_factory
from db.models import Event, Player, WeeklySeason
from repositories import events as E
from repositories import seasons as repo
from services import settings, wealth

STATUS_LIVE = "live"
STATUS_FINALIZED = "finalized"
PUBLICATION_PENDING = "pending"
PUBLICATION_PUBLISHED = "published"
PUBLICATION_SKIPPED = "skipped"

_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


@dataclass(frozen=True)
class PlayerState:
    user_id: int
    name: str
    length: int
    wealth: int


@dataclass(frozen=True)
class PlayerReport:
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

    @property
    def dick_average(self) -> float:
        return self.dick_total / self.dick_count if self.dick_count else 0.0


@dataclass(frozen=True)
class SeasonReport:
    chat_id: int
    season_number: int
    timezone: str
    starts_at: int
    ends_at: int
    tracking_since: int
    status: str
    is_partial: bool
    is_empty: bool
    publication_status: str
    published_at: int
    published_message_id: int
    length_start: int
    length_end: int
    wealth_start: int
    wealth_end: int
    emission: int
    best_dick_delta: int | None
    best_dick_user_id: int | None
    worst_dick_delta: int | None
    worst_dick_user_id: int | None
    players: tuple[PlayerReport, ...]

    @property
    def length_delta(self) -> int:
        return self.length_end - self.length_start

    @property
    def wealth_delta(self) -> int:
        return self.wealth_end - self.wealth_start

    @property
    def length_change_pct(self) -> float | None:
        return self.length_delta / self.length_start * 100 if self.length_start > 0 else None

    @property
    def wealth_change_pct(self) -> float | None:
        return self.wealth_delta / self.wealth_start * 100 if self.wealth_start > 0 else None

    @property
    def dick_leaders(self) -> tuple[PlayerReport, ...]:
        return tuple(
            sorted(
                (player for player in self.players if player.dick_count),
                key=lambda player: (
                    -player.dick_total,
                    -Fraction(player.dick_total, player.dick_count),
                    -player.active_days,
                    player.user_id,
                ),
            )
        )

    @property
    def wealth_leaders(self) -> tuple[PlayerReport, ...]:
        return tuple(
            sorted(
                (player for player in self.players if player.active_days or player.wealth_delta),
                key=lambda player: (
                    -player.wealth_delta,
                    -player.end_wealth,
                    -player.active_days,
                    player.user_id,
                ),
            )
        )


def _chat_lock(chat_id: int) -> asyncio.Lock:
    lock = _locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[chat_id] = lock
    return lock


def week_bounds(timestamp: int, zone: ZoneInfo) -> tuple[int, int]:
    local = datetime.fromtimestamp(timestamp, zone)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=7)
    return int(start.timestamp()), int(end.timestamp())


def _next_end(starts_at: int, zone: ZoneInfo) -> int:
    return int((datetime.fromtimestamp(starts_at, zone) + timedelta(days=7)).timestamp())


def competitive_wealth_delta(event: Event) -> int:
    """Return earned/lost wealth; transfers and administrative changes are zero."""
    meta = event.meta or {}
    if event.type == E.DICK:
        return int(event.delta) - int(meta.get("roll_debt", 0))
    if event.type == E.DUEL:
        return int(meta.get("profit", event.delta)) if meta.get("won") else int(event.delta)
    if event.type == E.DEPOSIT_INSURANCE:
        return int(event.delta)
    if event.type == E.DEPOSIT_INTEREST:
        return int(meta.get("interest", 0))
    if event.type == E.DEPOSIT_PENALTY:
        return -int(meta.get("penalty", 0)) - int(meta.get("forfeit_interest", 0))
    if event.type == E.CONFISCATION:
        return int(event.delta) or -int(meta.get("seized", 0))
    if event.type == E.LOAN_INTEREST:
        return -int(meta.get("interest", 0))
    if event.type == E.POKER_RESULT:
        return int(meta.get("net", 0))
    return 0


def _actual_wealth_delta(event: Event) -> int:
    if event.type == E.BASELINE:
        return int(event.size_after)
    if event.type in {E.ADMIN_ADJUST, E.HEALTH_REFORM}:
        return int(event.delta)
    return competitive_wealth_delta(event)


_LIQUID_EVENT_TYPES = {
    E.DICK,
    E.DUEL,
    E.ADMIN_ADJUST,
    E.DEPOSIT_INSURANCE,
    E.DEPOSIT_OPEN,
    E.DEPOSIT_WITHDRAW,
    E.LOAN_OPEN,
    E.LOAN_REPAY,
    E.LOAN_GARNISH,
    E.HEALTH_REFORM,
    E.POKER_BUYIN,
    E.POKER_TOPUP,
    E.POKER_CASHOUT,
}


def _liquid_delta(event: Event) -> int:
    if event.type == E.BASELINE:
        return int(event.size_after)
    if event.type == E.LOAN_GARNISH and (event.meta or {}).get("from_deposit"):
        return 0
    if event.type in _LIQUID_EVENT_TYPES:
        return int(event.delta)
    return 0


async def _state_at(chat_id: int, timestamp: int) -> dict[int, PlayerState]:
    """Reconstruct state immediately before events at ``timestamp``."""
    current_wealth = {row.user_id: row for row in await wealth.chat_rows(chat_id)}
    factory = get_session_factory()
    async with factory() as session:
        players = list(
            (await session.execute(select(Player).where(Player.chat_id == chat_id))).scalars()
        )
        events = list(
            (
                await session.execute(
                    select(Event)
                    .where(Event.chat_id == chat_id, Event.created_at >= timestamp)
                    .order_by(Event.created_at.desc(), Event.id.desc())
                )
            ).scalars()
        )

    values: dict[int, list[int | str]] = {}
    for player in players:
        values[int(player.user_id)] = [player.name or str(player.user_id), int(player.size), 0]
    for user_id, row in current_wealth.items():
        values.setdefault(user_id, [row.name or str(user_id), 0, 0])[2] = row.net
    for event in events:
        user_id = int(event.user_id)
        if user_id == 0:
            continue
        value = values.setdefault(user_id, [str(user_id), 0, 0])
        value[1] = int(value[1]) - _liquid_delta(event)
        value[2] = int(value[2]) - _actual_wealth_delta(event)
    return {
        user_id: PlayerState(user_id, str(value[0]), int(value[1]), int(value[2]))
        for user_id, value in values.items()
        if int(value[1]) or int(value[2]) or user_id in current_wealth
    }


async def _events_between(chat_id: int, starts_at: int, ends_at: int) -> list[Event]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(Event)
                    .where(
                        Event.chat_id == chat_id,
                        Event.created_at >= starts_at,
                        Event.created_at < ends_at,
                    )
                    .order_by(Event.created_at, Event.id)
                )
            ).scalars()
        )


def _report(season: WeeklySeason) -> SeasonReport:
    players = tuple(
        PlayerReport(
            user_id=int(row.user_id),
            name=row.name or str(row.user_id),
            start_length=int(row.start_length),
            end_length=int(row.end_length),
            start_wealth=int(row.start_wealth),
            end_wealth=int(row.end_wealth),
            dick_total=int(row.dick_total),
            dick_count=int(row.dick_count),
            active_days=int(row.active_days),
            best_dick_delta=row.best_dick_delta,
            worst_dick_delta=row.worst_dick_delta,
            wealth_delta=int(row.wealth_delta),
        )
        for row in season.players
    )
    return SeasonReport(
        chat_id=int(season.chat_id),
        season_number=int(season.season_number),
        timezone=season.timezone,
        starts_at=int(season.starts_at),
        ends_at=int(season.ends_at),
        tracking_since=int(season.tracking_since),
        status=season.status,
        is_partial=bool(season.is_partial),
        is_empty=bool(season.is_empty),
        publication_status=season.publication_status,
        published_at=int(season.published_at),
        published_message_id=int(season.published_message_id),
        length_start=int(season.length_start),
        length_end=int(season.length_end),
        wealth_start=int(season.wealth_start),
        wealth_end=int(season.wealth_end),
        emission=int(season.emission),
        best_dick_delta=season.best_dick_delta,
        best_dick_user_id=season.best_dick_user_id,
        worst_dick_delta=season.worst_dick_delta,
        worst_dick_user_id=season.worst_dick_user_id,
        players=players,
    )


def _seeds(states: dict[int, PlayerState]) -> tuple[repo.PlayerSeed, ...]:
    return tuple(
        repo.PlayerSeed(value.user_id, value.name, value.length, value.wealth)
        for value in states.values()
    )


async def _installation_time(now: int) -> int:
    # The season feature's first durable row is its installation watermark.
    # Older analytics watermarks predate seasons and must not turn the current
    # incomplete week into an official one.
    return now


async def _create_live(
    chat_id: int,
    *,
    starts_at: int,
    ends_at: int,
    tracking_since: int,
    timezone: str,
    season_number: int,
    is_partial: bool,
    created_at: int,
) -> WeeklySeason:
    states = await _state_at(chat_id, tracking_since if is_partial else starts_at)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        existing = await repo.get_live_in(session, chat_id)
        if existing is not None:
            return existing
        return await repo.create_live_in(
            session,
            chat_id=chat_id,
            season_number=season_number,
            timezone=timezone,
            starts_at=starts_at,
            ends_at=ends_at,
            tracking_since=tracking_since,
            is_partial=is_partial,
            players=_seeds(states),
            created_at=created_at,
        )


async def _ensure_initial(
    chat_id: int,
    *,
    now: int,
    timezone: str | None,
    installed_at: int | None,
) -> WeeklySeason:
    current = await repo.get_live(chat_id)
    if current is not None:
        return current
    zone = ZoneInfo(timezone) if timezone else await settings.resolve_tz(chat_id)
    starts_at, ends_at = week_bounds(now, zone)
    installed = installed_at if installed_at is not None else await _installation_time(now)
    complete = installed <= starts_at
    return await _create_live(
        chat_id,
        starts_at=starts_at,
        ends_at=ends_at,
        tracking_since=starts_at if complete else max(starts_at, installed),
        timezone=zone.key,
        season_number=1 if complete else 0,
        is_partial=not complete,
        created_at=now,
    )


async def _calculate_final(season: WeeklySeason) -> repo.SeasonFinal:
    zone = ZoneInfo(season.timezone)
    start = {
        int(row.user_id): PlayerState(
            int(row.user_id), row.name or str(row.user_id), row.start_length, row.start_wealth
        )
        for row in season.players
    }
    end = await _state_at(int(season.chat_id), int(season.ends_at))
    events = await _events_between(
        int(season.chat_id), int(season.tracking_since), int(season.ends_at)
    )
    dick: dict[int, list[int]] = defaultdict(list)
    active: dict[int, set[str]] = defaultdict(set)
    competitive: dict[int, int] = defaultdict(int)
    emission_events = [event for event in events if event.type == E.CORP_EMISSION]
    emission = sum(int((event.meta or {}).get("emitted", 0)) for event in emission_events)
    if not emission_events:
        emission = sum(
            int((event.meta or {}).get("emitted", 0)) for event in events if event.type == E.DICK
        )
    for event in events:
        user_id = int(event.user_id)
        if user_id == 0:
            continue
        delta = competitive_wealth_delta(event)
        competitive[user_id] += delta
        if event.type == E.DICK:
            dick[user_id].append(E.dick_game_delta(event))
        if event.type == E.DICK or delta:
            day = datetime.fromtimestamp(event.created_at, zone).date().isoformat()
            active[user_id].add(day)

    current_names = {value.user_id: value.name for value in await wealth.chat_rows(season.chat_id)}
    user_ids = set(start) | set(end) | set(dick) | set(competitive)
    players: list[repo.PlayerFinal] = []
    for user_id in sorted(user_ids):
        before = start.get(user_id, PlayerState(user_id, str(user_id), 0, 0))
        after = end.get(user_id, PlayerState(user_id, before.name, 0, 0))
        rolls = dick.get(user_id, [])
        players.append(
            repo.PlayerFinal(
                user_id=user_id,
                name=current_names.get(user_id, after.name or before.name or str(user_id)),
                start_length=before.length,
                end_length=after.length,
                start_wealth=before.wealth,
                end_wealth=after.wealth,
                dick_total=sum(rolls),
                dick_count=len(rolls),
                active_days=len(active.get(user_id, set())),
                best_dick_delta=max(rolls) if rolls else None,
                worst_dick_delta=min(rolls) if rolls else None,
                wealth_delta=competitive.get(user_id, 0),
            )
        )
    rolls_with_users = [
        (E.dick_game_delta(event), int(event.user_id)) for event in events if event.type == E.DICK
    ]
    best = max(rolls_with_users, key=lambda value: (value[0], -value[1]), default=None)
    worst = min(rolls_with_users, key=lambda value: (value[0], value[1]), default=None)
    # Auto-publication is driven by the bot's core daily ritual: a week with
    # duels, poker or bank movements but no /dick presses is still a quiet
    # season. It remains archived and its secondary statistics stay available.
    is_empty = not rolls_with_users
    return repo.SeasonFinal(
        is_empty=is_empty,
        length_end=sum(value.length for value in end.values()),
        wealth_end=sum(value.wealth for value in end.values()),
        emission=emission,
        best_dick_delta=best[0] if best else None,
        best_dick_user_id=best[1] if best else None,
        worst_dick_delta=worst[0] if worst else None,
        worst_dick_user_id=worst[1] if worst else None,
        players=tuple(players),
    )


async def _advance_once(chat_id: int, live: WeeklySeason, *, finalized_at: int) -> WeeklySeason:
    effective = await settings.get_effective(chat_id)
    next_zone = ZoneInfo(effective.tz)
    if live.is_partial:
        states = await _state_at(chat_id, live.ends_at)
        factory = get_session_factory()
        async with factory() as session, session.begin():
            current = await repo.get_live_in(session, chat_id)
            if current is None:
                raise RuntimeError("live season disappeared")
            if not current.is_partial:
                return current
            starts_at = int(current.ends_at)
            await repo.discard_partial_in(session, current)
            return await repo.create_live_in(
                session,
                chat_id=chat_id,
                season_number=1,
                timezone=next_zone.key,
                starts_at=starts_at,
                ends_at=_next_end(starts_at, next_zone),
                tracking_since=starts_at,
                is_partial=False,
                players=_seeds(states),
                created_at=finalized_at,
            )

    result = await _calculate_final(live)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        current = await repo.get_live_in(session, chat_id)
        if current is None:
            raise RuntimeError("live season disappeared")
        if current.id != live.id:
            return current
        await repo.finalize_in(session, current, result, finalized_at=finalized_at)
        starts_at = int(current.ends_at)
        number = await repo.next_number_in(session, chat_id)
        end_states = {
            value.user_id: PlayerState(
                value.user_id, value.name, value.end_length, value.end_wealth
            )
            for value in result.players
        }
        return await repo.create_live_in(
            session,
            chat_id=chat_id,
            season_number=number,
            timezone=next_zone.key,
            starts_at=starts_at,
            ends_at=_next_end(starts_at, next_zone),
            tracking_since=starts_at,
            is_partial=False,
            players=_seeds(end_states),
            created_at=finalized_at,
        )


async def finalize_due(
    chat_id: int,
    *,
    now: int | None = None,
    timezone: str | None = None,
    installed_at: int | None = None,
) -> tuple[SeasonReport, ...]:
    """Archive all complete official weeks and leave one live season."""
    timestamp = int(time.time()) if now is None else now
    async with _chat_lock(chat_id):
        live = await _ensure_initial(
            chat_id, now=timestamp, timezone=timezone, installed_at=installed_at
        )
        finalized: list[SeasonReport] = []
        while live.ends_at <= timestamp:
            was_partial = bool(live.is_partial)
            live = await _advance_once(chat_id, live, finalized_at=timestamp)
            if not was_partial:
                archived = await repo.get_by_number(chat_id, live.season_number - 1)
                if archived is not None:
                    finalized.append(_report(archived))
        return tuple(finalized)


async def ensure_live(
    chat_id: int,
    *,
    now: int | None = None,
    timezone: str | None = None,
    installed_at: int | None = None,
) -> SeasonReport:
    await finalize_due(chat_id, now=now, timezone=timezone, installed_at=installed_at)
    live = await repo.get_live(chat_id)
    if live is None:
        raise RuntimeError("could not create live season")
    return await _live_report(live, int(time.time()) if now is None else now)


async def _live_report(live: WeeklySeason, now: int) -> SeasonReport:
    effective_end = min(now + 1, int(live.ends_at))
    if effective_end <= live.tracking_since:
        return _report(live)
    # Reuse final calculation against a detached copy of the live bounds. It
    # never writes the calculated values back, so refresh remains explicit.
    original_end = live.ends_at
    live.ends_at = effective_end
    try:
        result = await _calculate_final(live)
    finally:
        live.ends_at = original_end
    base = _report(live)
    players = tuple(
        PlayerReport(
            user_id=value.user_id,
            name=value.name,
            start_length=value.start_length,
            end_length=value.end_length,
            start_wealth=value.start_wealth,
            end_wealth=value.end_wealth,
            dick_total=value.dick_total,
            dick_count=value.dick_count,
            active_days=value.active_days,
            best_dick_delta=value.best_dick_delta,
            worst_dick_delta=value.worst_dick_delta,
            wealth_delta=value.wealth_delta,
        )
        for value in result.players
    )
    return SeasonReport(
        **{
            **base.__dict__,
            "is_empty": result.is_empty,
            "length_end": result.length_end,
            "wealth_end": result.wealth_end,
            "emission": result.emission,
            "best_dick_delta": result.best_dick_delta,
            "best_dick_user_id": result.best_dick_user_id,
            "worst_dick_delta": result.worst_dick_delta,
            "worst_dick_user_id": result.worst_dick_user_id,
            "players": players,
        }
    )


async def get_live_report(chat_id: int, *, now: int | None = None) -> SeasonReport | None:
    live = await repo.get_live(chat_id)
    if live is None:
        return None
    return await _live_report(live, int(time.time()) if now is None else now)


async def get_report(chat_id: int, season_number: int) -> SeasonReport | None:
    season = await repo.get_by_number(chat_id, season_number)
    if season is None or season.is_partial:
        return None
    return _report(season)


async def list_reports(
    chat_id: int, *, offset: int = 0, limit: int = 20
) -> tuple[SeasonReport, ...]:
    return tuple(
        _report(season) for season in await repo.list_finalized(chat_id, offset=offset, limit=limit)
    )


async def mark_published(
    chat_id: int, season_number: int, *, message_id: int, published_at: int | None = None
) -> bool:
    return await repo.mark_published(
        chat_id, season_number, message_id=message_id, published_at=published_at
    )
