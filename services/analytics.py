"""Authorised-dashboard data, chart and CSV generation.

The event journal is canonical.  This module deliberately contains no Telegram
objects so reports can also be rendered by the weekly digest and tests.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import math
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from db.engine import get_session_factory
from db.models import (
    AnalyticsState,
    Chat,
    ChatCorporation,
    CorporationLedger,
    Deposit,
    Event,
    Loan,
    Player,
    PokerSeat,
    PokerTable,
)
from repositories import events as E
from services import settings, wealth
from services.global_settings import get_config_sync

PERIODS = {"d": "Сегодня", "7": "7 дней", "30": "30 дней", "a": "Всё время"}
SECTIONS = {
    "overview": "Обзор",
    "growth": "Рост",
    "duels": "Дуэли",
    "bank": "Банк",
    "poker": "Покер",
    "health": "PISYAGO и болезни",
    "leaders": "Лидеры и сравнение",
    "corp": "Корпорация",
}

_render_slots = asyncio.Semaphore(2)
_png_cache: dict[tuple, tuple[float, bytes]] = {}
_PNG_CACHE_TTL = 120
_PNG_CACHE_MAX = 32


@dataclass(frozen=True)
class Scope:
    kind: str  # user, personal, chat, leaderboard, global
    chat_id: int = 0
    user_id: int = 0


@dataclass
class Dashboard:
    title: str
    section: str
    period: str
    metrics: list[tuple[str, str]]
    labels: list[str]
    values: list[float]
    chart_title: str
    chart_ylabel: str = "см"
    note: str = ""
    chart_kind: str = "bar"
    series: list[tuple[str, list[float]]] = field(default_factory=list)


SIZE_EVENT_TYPES = {
    E.BASELINE,
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


def _system_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ", "Europe/Moscow"))


async def _zone(scope: Scope) -> ZoneInfo:
    if scope.chat_id:
        return await settings.resolve_tz(scope.chat_id)
    return _system_tz()


def _since(period: str, zone: ZoneInfo, now: int) -> int | None:
    local = datetime.fromtimestamp(now, zone)
    if period == "d":
        return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if period == "7":
        return int((local - timedelta(days=7)).timestamp())
    if period == "30":
        return int((local - timedelta(days=30)).timestamp())
    return None


def _event_matches(scope: Scope, stmt):
    if scope.kind == "user":
        return stmt.where(Event.chat_id == scope.chat_id, Event.user_id == scope.user_id)
    if scope.kind == "personal":
        return stmt.where(Event.user_id == scope.user_id)
    if scope.kind in {"chat", "leaderboard"}:
        return stmt.where(Event.chat_id == scope.chat_id)
    return stmt


async def _events(scope: Scope, period: str, zone: ZoneInfo, now: int) -> list[Event]:
    stmt = _event_matches(scope, select(Event))
    since = _since(period, zone, now)
    if since is not None:
        stmt = stmt.where(Event.created_at >= since)
    stmt = stmt.where(Event.created_at <= now).order_by(Event.created_at)
    factory = get_session_factory()
    async with factory() as session:
        return list((await session.execute(stmt)).scalars().all())


async def _snapshot(scope: Scope) -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        player_stmt = select(func.count(Player.user_id), func.coalesce(func.sum(Player.size), 0))
        dep_stmt = select(
            func.count(Deposit.user_id),
            func.coalesce(func.sum(Deposit.principal), 0),
            func.coalesce(func.sum(Deposit.accrued), 0),
        )
        loan_stmt = select(
            func.count(Loan.user_id),
            func.coalesce(func.sum(Loan.principal), 0),
            func.coalesce(func.sum(Loan.accrued_interest), 0),
            func.coalesce(func.sum(Loan.roll_debt_principal), 0),
            func.coalesce(func.sum(case((Loan.defaulted.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((Loan.due_at <= int(time.time()), 1), else_=0)), 0),
        )
        poker_stmt = (
            select(func.coalesce(func.sum(PokerSeat.stack + PokerSeat.committed), 0))
            .join(PokerTable, PokerTable.table_id == PokerSeat.table_id)
            .where(PokerSeat.status == "active", PokerTable.mode == "money")
        )
        if scope.kind == "user":
            player_stmt = player_stmt.where(
                Player.chat_id == scope.chat_id, Player.user_id == scope.user_id
            )
            dep_stmt = dep_stmt.where(
                Deposit.chat_id == scope.chat_id, Deposit.user_id == scope.user_id
            )
            loan_stmt = loan_stmt.where(
                Loan.chat_id == scope.chat_id, Loan.user_id == scope.user_id
            )
            poker_stmt = poker_stmt.where(
                PokerTable.chat_id == scope.chat_id, PokerSeat.user_id == scope.user_id
            )
        elif scope.kind == "personal":
            player_stmt = player_stmt.where(Player.user_id == scope.user_id)
            dep_stmt = dep_stmt.where(Deposit.user_id == scope.user_id)
            loan_stmt = loan_stmt.where(Loan.user_id == scope.user_id)
            poker_stmt = poker_stmt.where(PokerSeat.user_id == scope.user_id)
        elif scope.kind in {"chat", "leaderboard"}:
            player_stmt = player_stmt.where(Player.chat_id == scope.chat_id)
            dep_stmt = dep_stmt.where(Deposit.chat_id == scope.chat_id)
            loan_stmt = loan_stmt.where(Loan.chat_id == scope.chat_id)
            poker_stmt = poker_stmt.where(PokerTable.chat_id == scope.chat_id)
        players, liquid = (await session.execute(player_stmt)).one()
        depositors, deposits, dep_interest = (await session.execute(dep_stmt)).one()
        borrowers, loans, loan_interest, roll_debt, defaults, overdue = (
            await session.execute(loan_stmt)
        ).one()
        poker = (await session.execute(poker_stmt)).scalar_one()
        return {
            "players": int(players),
            "liquid": int(liquid),
            "deposits": int(deposits),
            "depositors": int(depositors),
            "deposit_interest": int(dep_interest),
            "loans": int(loans),
            "loan_interest": int(loan_interest),
            "roll_debt": int(roll_debt),
            "defaults": int(defaults),
            "borrowers": int(borrowers),
            "overdue": int(overdue),
            "poker": int(poker),
        }


async def _title(scope: Scope) -> str:
    factory = get_session_factory()
    async with factory() as session:
        if scope.kind in {"user", "personal"}:
            stmt = select(Player.name).where(Player.user_id == scope.user_id)
            if scope.kind == "user":
                stmt = stmt.where(Player.chat_id == scope.chat_id)
            name = (await session.execute(stmt.limit(1))).scalar_one_or_none()
            return name or str(scope.user_id)
        if scope.kind in {"chat", "leaderboard"}:
            chat = await session.get(Chat, scope.chat_id)
            return chat.title if chat and chat.title else str(scope.chat_id)
    return "Вся система"


async def _player_names(user_ids: set[int], chat_id: int = 0) -> dict[int, str]:
    if not user_ids:
        return {}
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Player.user_id, Player.name).where(Player.user_id.in_(user_ids))
        if chat_id:
            stmt = stmt.where(Player.chat_id == chat_id)
        rows = (await session.execute(stmt)).all()
    names: dict[int, str] = {}
    for user_id, name in rows:
        names.setdefault(int(user_id), str(name or user_id))
    return names


def _num(value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return f"{int(value):,}".replace(",", " ")


def _percent(part: int, total: int) -> str:
    return f"{part / total * 100:.1f}%" if total else "0.0%"


def _one(_event: Event) -> int:
    return 1


def _delta(event: Event) -> int:
    return event.delta


def _bank_value(event: Event) -> int:
    return abs(event.delta) or int(
        (event.meta or {}).get("interest", (event.meta or {}).get("amount", 0))
    )


def _poker_net(event: Event) -> int:
    return int((event.meta or {}).get("net", 0))


def _bucket_labels(
    events: list[Event], period: str, zone: ZoneInfo
) -> tuple[list[str], dict[int, int]]:
    keys: list[str] = []
    indices: dict[str, int] = {}
    mapping: dict[int, int] = {}
    for event in events:
        dt = datetime.fromtimestamp(event.created_at, zone)
        if period == "d":
            key = dt.strftime("%H:00")
        elif (
            period == "a" and events and events[-1].created_at - events[0].created_at > 180 * 86400
        ):
            key = dt.strftime("%Y-%m")
        else:
            key = dt.strftime("%d.%m")
        if key not in indices:
            indices[key] = len(keys)
            keys.append(key)
        mapping[event.id] = indices[key]
    if len(keys) > 60:
        # Keep charts legible; CSV still receives the same consolidated buckets.
        stride = (len(keys) + 59) // 60
        compact = [keys[i] for i in range(0, len(keys), stride)]
        old_to_new = {i: min(i // stride, len(compact) - 1) for i in range(len(keys))}
        mapping = {eid: old_to_new[index] for eid, index in mapping.items()}
        keys = compact
    return keys, mapping


def _series(
    events: list[Event], period: str, zone: ZoneInfo, value_fn
) -> tuple[list[str], list[float]]:
    labels, buckets = _bucket_labels(events, period, zone)
    values = [0.0] * len(labels)
    for event in events:
        values[buckets[event.id]] += float(value_fn(event))
    return labels, values


def _line_labels(times: list[int], period: str, zone: ZoneInfo) -> list[str]:
    short_all = period == "a" and bool(times) and times[-1] - times[0] <= 60 * 86400
    pattern = (
        "%H:%M" if period == "d" else ("%d.%m" if period in {"7", "30"} or short_all else "%m.%Y")
    )
    return [datetime.fromtimestamp(timestamp, zone).strftime(pattern) for timestamp in times]


def _sample_times(times: list[int], maximum: int = 60) -> list[int]:
    ordered = sorted(set(times))
    if len(ordered) <= maximum:
        return ordered
    indexes = {round(index * (len(ordered) - 1) / (maximum - 1)) for index in range(maximum)}
    return [ordered[index] for index in sorted(indexes)]


def _period_times(
    events: list[Event], period: str, since: int | None, now: int, zone: ZoneInfo
) -> list[int]:
    if since is not None:
        cursor = datetime.fromtimestamp(since, zone)
        step = timedelta(hours=1) if period == "d" else timedelta(days=1)
        points: list[int] = []
        while cursor.timestamp() < now:
            points.append(int(cursor.timestamp()))
            cursor += step
        return _sample_times([*points, now])
    if not events:
        return [now]
    first = datetime.fromtimestamp(events[0].created_at, zone)
    current = datetime.fromtimestamp(now, zone)
    if (current - first).days <= 60:
        cursor = first.replace(hour=0, minute=0, second=0, microsecond=0)
        points = []
        while cursor < current:
            points.append(int(cursor.timestamp()))
            cursor += timedelta(days=1)
        return _sample_times([*points, now])
    cursor = first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    points = []
    while cursor < current:
        points.append(int(cursor.timestamp()))
        year, month = cursor.year, cursor.month + 1
        if month == 13:
            year, month = year + 1, 1
        cursor = cursor.replace(year=year, month=month)
    return _sample_times([*points, now])


def _wealth_delta(event: Event) -> int:
    meta = event.meta or {}
    if event.type == E.DICK:
        return int(event.delta) - int(meta.get("roll_debt", 0))
    if event.type == E.DUEL:
        return int(meta.get("profit", event.delta)) if meta.get("won") else int(event.delta)
    if event.type in {E.ADMIN_ADJUST, E.HEALTH_REFORM, E.DEPOSIT_INSURANCE}:
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


def _wealth_timeline(
    events: list[Event],
    entity_ids: list[int],
    names: dict[int, str],
    current: dict[int, int],
    *,
    entity_key,
    since: int | None,
    now: int,
    period: str,
    zone: ZoneInfo,
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    if not entity_ids:
        return [], []
    ordered_events = sorted(events, key=lambda event: (event.created_at, event.id))
    times = _period_times(ordered_events, period, since, now, zone)
    values = {entity_id: [] for entity_id in entity_ids}
    for timestamp in times:
        for entity_id in entity_ids:
            if entity_id not in current:
                values[entity_id].append(math.nan)
                continue
            future_delta = sum(
                _wealth_delta(event)
                for event in ordered_events
                if event.created_at > timestamp and int(entity_key(event)) == entity_id
            )
            values[entity_id].append(float(current[entity_id] - future_delta))
    return _line_labels(times, period, zone), [
        (names.get(entity_id, str(entity_id))[:24], values[entity_id]) for entity_id in entity_ids
    ]


async def _growth_timeline(
    scope: Scope, period: str, zone: ZoneInfo, now: int
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    if scope.kind in {"chat", "global"}:
        return await _aggregate_growth_timeline(scope, period, zone, now)
    wealth_rows = await wealth.rows(
        chat_id=scope.chat_id if scope.kind == "user" else None,
        user_id=scope.user_id,
    )
    factory = get_session_factory()
    async with factory() as session:
        event_stmt = select(Event).where(
            Event.user_id == scope.user_id,
            Event.created_at <= now,
        )
        if scope.kind == "user":
            event_stmt = event_stmt.where(Event.chat_id == scope.chat_id)
        events = list((await session.execute(event_stmt.order_by(Event.created_at))).scalars())
        player_chat_ids = {row.chat_id for row in wealth_rows}
        event_chat_ids = {int(event.chat_id) for event in events}
        chat_ids = sorted(player_chat_ids | event_chat_ids)
        if scope.kind == "user":
            chat_ids = [scope.chat_id]
        title_rows = (
            (
                await session.execute(
                    select(Chat.chat_id, Chat.title).where(Chat.chat_id.in_(chat_ids))
                )
            ).all()
            if chat_ids
            else []
        )
    current: dict[int, int] = {}
    for event in events:
        if event.type in SIZE_EVENT_TYPES:
            current[int(event.chat_id)] = int(event.size_after)
    current.update({row.chat_id: row.net for row in wealth_rows})
    chat_names = {int(chat_id): str(title or chat_id) for chat_id, title in title_rows}
    if scope.kind == "user":
        chat_names[scope.chat_id] = "Чистое состояние"
        chat_ids = [scope.chat_id]
    return _wealth_timeline(
        events,
        chat_ids,
        chat_names,
        current,
        entity_key=lambda event: event.chat_id,
        since=_since(period, zone, now),
        now=now,
        period=period,
        zone=zone,
    )


async def _aggregate_growth_timeline(
    scope: Scope, period: str, zone: ZoneInfo, now: int
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    wealth_rows = await wealth.rows(chat_id=scope.chat_id if scope.kind == "chat" else None)
    factory = get_session_factory()
    async with factory() as session:
        event_stmt = select(Event).where(
            Event.created_at <= now,
            Event.user_id != 0,
        )
        if scope.kind == "chat":
            event_stmt = event_stmt.where(Event.chat_id == scope.chat_id)
        events = list(
            (await session.execute(event_stmt.order_by(Event.created_at, Event.id))).scalars()
        )

    times = _period_times(events, period, _since(period, zone, now), now, zone)
    current = {(row.chat_id, row.user_id): float(row.net) for row in wealth_rows}
    persisted = set(current)
    for event in events:
        key = (int(event.chat_id), int(event.user_id))
        if event.type in SIZE_EVENT_TYPES and key not in persisted:
            current[key] = float(event.size_after)
    values: list[float] = []
    for timestamp in times:
        future_delta = sum(_wealth_delta(event) for event in events if event.created_at > timestamp)
        values.append(sum(current.values()) - future_delta)
    return _line_labels(times, period, zone), [("Общее чистое состояние", values)]


async def _chat_leader_timeline(
    chat_id: int, period: str, zone: ZoneInfo, now: int
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    leaders = (await wealth.chat_rows(chat_id))[:10]
    user_ids = [player.user_id for player in leaders]
    factory = get_session_factory()
    async with factory() as session:
        events = (
            list(
                (
                    await session.execute(
                        select(Event)
                        .where(
                            Event.chat_id == chat_id,
                            Event.user_id.in_(user_ids),
                            Event.created_at <= now,
                        )
                        .order_by(Event.created_at)
                    )
                ).scalars()
            )
            if user_ids
            else []
        )
    names = {player.user_id: player.name for player in leaders}
    duplicates = Counter(names.values())
    for user_id, name in list(names.items()):
        if duplicates[name] > 1:
            names[user_id] = f"{name} · {str(user_id)[-4:]}"
    current = {player.user_id: player.net for player in leaders}
    return _wealth_timeline(
        events,
        user_ids,
        names,
        current,
        entity_key=lambda event: event.user_id,
        since=_since(period, zone, now),
        now=now,
        period=period,
        zone=zone,
    )


async def dashboard(
    scope: Scope, section: str, period: str, *, now: int | None = None
) -> Dashboard:
    if section not in SECTIONS or period not in PERIODS:
        raise ValueError("unknown dashboard page")
    now = int(time.time()) if now is None else now
    zone = await _zone(scope)
    events = await _events(scope, period, zone, now)
    snap = await _snapshot(scope)
    title = await _title(scope)
    watermark = await detailed_since()
    metrics: list[tuple[str, str]] = []
    chart_title = "Активность"
    chart_ylabel = "события"
    relevant = events
    value_fn = _one
    chart_kind = "bar"
    chart_series: list[tuple[str, list[float]]] = []

    if section == "overview":
        assets = snap["liquid"] + snap["deposits"] + snap["deposit_interest"] + snap["poker"]
        debt = snap["loans"] + snap["loan_interest"]
        active = len({event.user_id for event in events if event.user_id})
        active_days = len(
            {
                datetime.fromtimestamp(event.created_at, zone).date()
                for event in events
                if event.type == E.DICK
            }
        )
        metrics = [
            ("Ликвидно", f"{_num(snap['liquid'])} см"),
            ("Вклады", f"{_num(snap['deposits'] + snap['deposit_interest'])} см"),
            ("Покер", f"{_num(snap['poker'])} см"),
            ("Долг", f"{_num(debt)} см"),
            ("Чистое состояние", f"{_num(assets - debt)} см"),
            (
                "Активных дней" if scope.kind in {"user", "personal"} else "Активных игроков",
                _num(active_days if scope.kind in {"user", "personal"} else active),
            ),
        ]
        chart_title = "Все действия по времени"
    elif section == "growth":
        relevant = [event for event in events if event.type == E.DICK]
        rolled = [int((event.meta or {}).get("rolled", event.delta)) for event in relevant]
        positive = sum(value > 0 for value in rolled)
        negative = sum(value < 0 for value in rolled)
        net = sum(event.delta for event in relevant)
        earned = sum(max(0, event.delta) for event in relevant)
        lost = sum(max(0, -event.delta) for event in relevant)
        metrics = [
            ("Нажатий", _num(len(relevant))),
            ("Плюсов / минусов", f"{positive} / {negative}"),
            ("Чистый итог", f"{net:+d} см"),
            ("Заработано / потеряно", f"{earned} / {lost} см"),
            (
                "Средний заработок",
                f"{net / len(relevant):+.1f} см" if relevant else "—",
            ),
            ("Средний бросок", f"{sum(rolled) / len(rolled):+.1f} см" if rolled else "—"),
            ("Медиана", f"{median(rolled):+.1f} см" if rolled else "—"),
            ("Лучший / худший", f"{max(rolled):+d} / {min(rolled):+d}" if rolled else "—"),
            ("Эмиссия", f"{sum(int((e.meta or {}).get('emitted', 0)) for e in relevant)} см"),
            ("Срезано", f"{sum(int((e.meta or {}).get('clipped', 0)) for e in relevant)} см"),
        ]
        chart_title = "Чистое состояние по времени"
        chart_ylabel = "см"
        labels, chart_series = await _growth_timeline(scope, period, zone, now)
        values = chart_series[0][1] if chart_series else []
        chart_kind = "line"
    elif section == "duels":
        relevant = [event for event in events if event.type == E.DUEL]
        wins = sum(bool((event.meta or {}).get("won")) for event in relevant)
        stakes = sum(int((event.meta or {}).get("stake", 0)) for event in relevant)
        tax = sum(int((event.meta or {}).get("tax", 0)) for event in relevant)
        victims = Counter(
            int((event.meta or {}).get("opponent_id", 0))
            for event in relevant
            if (event.meta or {}).get("won") and (event.meta or {}).get("opponent_id")
        )
        offenders = Counter(
            int((event.meta or {}).get("opponent_id", 0))
            for event in relevant
            if not (event.meta or {}).get("won") and (event.meta or {}).get("opponent_id")
        )
        opponent_names = await _player_names(
            set(victims) | set(offenders), scope.chat_id if scope.kind == "user" else 0
        )
        metrics = [
            ("Дуэлей", _num(len(relevant) // (1 if scope.kind in {"user", "personal"} else 2))),
            ("Побед / поражений", f"{wins} / {len(relevant) - wins}"),
            ("Победы", _percent(wins, len(relevant))),
            ("Оборот ставок", f"{_num(stakes)} см"),
            ("Налог", f"{_num(tax)} см"),
            ("Чистый итог", f"{sum(e.delta for e in relevant):+d} см"),
            (
                "Любимая жертва",
                opponent_names.get(victims.most_common(1)[0][0], str(victims.most_common(1)[0][0]))
                if victims
                else "—",
            ),
            (
                "Главный обидчик",
                opponent_names.get(
                    offenders.most_common(1)[0][0], str(offenders.most_common(1)[0][0])
                )
                if offenders
                else "—",
            ),
        ]
        chart_title = "Результат дуэлей"
        chart_ylabel = "см"
        value_fn = _delta
    elif section == "bank":
        kinds = {
            E.DEPOSIT_OPEN,
            E.DEPOSIT_WITHDRAW,
            E.DEPOSIT_INTEREST,
            E.DEPOSIT_PENALTY,
            E.DEPOSIT_INSURANCE,
            E.CONFISCATION,
            E.LOAN_OPEN,
            E.LOAN_REPAY,
            E.LOAN_INTEREST,
            E.LOAN_GARNISH,
            E.LOAN_DEFAULT,
            E.DICK_DEBT,
        }
        relevant = [event for event in events if event.type in kinds]
        sums = defaultdict(int)
        counts = Counter(event.type for event in relevant)
        for event in relevant:
            meta = event.meta or {}
            sums[event.type] += abs(event.delta) or int(
                meta.get("interest", meta.get("amount", meta.get("seized", 0)))
            )
        repayments = [event for event in relevant if event.type == E.LOAN_REPAY]
        cleared = sum(bool((event.meta or {}).get("cleared")) for event in repayments)
        loan_opens = [event for event in relevant if event.type == E.LOAN_OPEN]
        interest_paid = sum(int((event.meta or {}).get("interest", 0)) for event in repayments)
        debt = snap["loans"] + snap["loan_interest"]
        metrics = [
            ("Вкладов открыто", f"{counts[E.DEPOSIT_OPEN]} · {_num(sums[E.DEPOSIT_OPEN])} см"),
            ("Процентов получено", f"{_num(sums[E.DEPOSIT_INTEREST])} см"),
            ("Штрафы и конфискации", f"{_num(sums[E.DEPOSIT_PENALTY] + sums[E.CONFISCATION])} см"),
            ("Кредитов взято", f"{counts[E.LOAN_OPEN]} · {_num(sums[E.LOAN_OPEN])} см"),
            (
                "Средний кредит",
                f"{sum(event.delta for event in loan_opens) / len(loan_opens):.1f} см"
                if loan_opens
                else "—",
            ),
            ("Погашено", f"{_num(sums[E.LOAN_REPAY] + sums[E.LOAN_GARNISH])} см"),
            ("Процентов уплачено", f"{_num(interest_paid)} см"),
            ("Закрыто добровольно", f"{cleared}/{len(repayments)}"),
            ("Долг сейчас", f"{_num(debt)} см"),
            (
                "Должники / просрочили",
                f"{snap['borrowers']} / {snap['overdue']}",
            ),
            (
                "Средний долг",
                f"{debt / snap['borrowers']:.1f} см" if snap["borrowers"] else "—",
            ),
            ("Дефолтов за период", _num(counts[E.LOAN_DEFAULT])),
        ]
        chart_title = "Денежные операции банка"
        chart_ylabel = "см"
        value_fn = _bank_value
    elif section == "poker":
        relevant = [event for event in events if event.type == E.POKER_RESULT]
        actions = [event for event in events if event.type == E.POKER_ACTION]
        wins = sum(bool((event.meta or {}).get("won_pot")) for event in relevant)
        hands = {
            ((event.meta or {}).get("table_id"), (event.meta or {}).get("hand_no"))
            for event in relevant
        }
        net = sum(int((event.meta or {}).get("net", 0)) for event in relevant)
        vpip_hands = {
            ((e.meta or {}).get("table_id"), (e.meta or {}).get("hand_no"))
            for e in actions
            if (e.meta or {}).get("street") == "preflop"
            and (e.meta or {}).get("action") in {"call", "raise"}
        }
        pfr_hands = {
            ((e.meta or {}).get("table_id"), (e.meta or {}).get("hand_no"))
            for e in actions
            if (e.meta or {}).get("street") == "preflop" and (e.meta or {}).get("action") == "raise"
        }
        raises = sum((event.meta or {}).get("action") == "raise" for event in actions)
        calls = sum((event.meta or {}).get("action") == "call" for event in actions)
        timeouts = sum(bool((event.meta or {}).get("timed_out")) for event in actions)
        metrics = [
            ("Раздач", _num(len(hands))),
            ("Участий в раздачах", _num(len(relevant))),
            ("Банков выиграно", _num(wins)),
            ("Чистый итог", f"{net:+d} см"),
            ("VPIP", _percent(len(vpip_hands), len(relevant))),
            ("PFR", _percent(len(pfr_hands), len(relevant))),
            ("Рейзов", _num(raises)),
            ("Агрессия", f"{raises / calls:.2f}" if calls else ("∞" if raises else "0.00")),
            ("Тайм-аутов", _num(timeouts)),
        ]
        chart_title = "Результат покера"
        chart_ylabel = "см"
        value_fn = _poker_net
    elif section == "health":
        relevant = [event for event in events if event.type in {E.PISYAGO, E.INFECTION}]
        pisyago = [event for event in relevant if event.type == E.PISYAGO]
        infections = [event for event in relevant if event.type == E.INFECTION]
        diseases = Counter(
            (event.meta or {}).get("disease_id", "неизвестно") for event in infections
        )
        metrics = [
            ("Срабатываний PISYAGO", _num(len(pisyago))),
            (
                "Прикрыто страховкой",
                f"{sum(int((e.meta or {}).get('covered', 0)) for e in pisyago)} см",
            ),
            ("Осталось долгом", f"{sum(int((e.meta or {}).get('debt', 0)) for e in pisyago)} см"),
            ("Заражений", _num(len(infections))),
            ("Частая зараза", str(diseases.most_common(1)[0][0]) if diseases else "—"),
        ]
        chart_title = "Страховка и заражения"
        chart_ylabel = "случаи"
    elif section == "leaders":
        return await _leaders_dashboard(scope, period, events, title, zone, now)
    else:
        return await _corporation_dashboard(scope, period, zone, now, title)

    if section != "growth":
        labels, values = _series(relevant, period, zone, value_fn)
    note = ""
    if section in {"bank", "poker"} and watermark and (_since(period, zone, now) or 0) < watermark:
        note = f"Детальная разбивка гарантированно точна с {datetime.fromtimestamp(watermark, zone):%d.%m.%Y}."
    return Dashboard(
        title,
        section,
        period,
        metrics,
        labels,
        values,
        chart_title,
        chart_ylabel,
        note,
        chart_kind,
        chart_series,
    )


async def _corporation_dashboard(
    scope: Scope, period: str, zone: ZoneInfo, now: int, title: str
) -> Dashboard:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(CorporationLedger).where(CorporationLedger.created_at <= now)
        corp_stmt = select(
            func.coalesce(func.sum(ChatCorporation.balance), 0),
            func.coalesce(func.sum(ChatCorporation.insurance_reserve), 0),
            func.coalesce(func.sum(ChatCorporation.total_emission), 0),
            func.coalesce(func.sum(ChatCorporation.total_bailin), 0),
            func.coalesce(func.sum(ChatCorporation.bankruptcy_count), 0),
        )
        liability_stmt = select(func.coalesce(func.sum(Deposit.principal + Deposit.accrued), 0))
        if scope.chat_id:
            stmt = stmt.where(CorporationLedger.chat_id == scope.chat_id)
            corp_stmt = corp_stmt.where(ChatCorporation.chat_id == scope.chat_id)
            liability_stmt = liability_stmt.where(Deposit.chat_id == scope.chat_id)
        since = _since(period, zone, now)
        if since is not None:
            stmt = stmt.where(CorporationLedger.created_at >= since)
        rows = list((await session.execute(stmt.order_by(CorporationLedger.created_at))).scalars())
        balance, reserve, emission, bailin, bankruptcies = (await session.execute(corp_stmt)).one()
        liability = int((await session.execute(liability_stmt)).scalar_one())
    income = sum(max(0, row.cash_delta) for row in rows)
    expense = sum(max(0, -row.cash_delta) for row in rows)
    reasons = Counter()
    for row in rows:
        reasons[row.reason] += abs(row.cash_delta) + max(0, row.reserve_delta)
    reserve_required = liability * get_config_sync().corp_liquidity_reserve_pct // 100
    metrics = [
        ("Баланс", f"{_num(balance)} см"),
        ("Страховой резерв", f"{_num(reserve)} см"),
        ("Обязательства по вкладам", f"{_num(liability)} см"),
        ("Обязательный резерв", f"{_num(reserve_required)} см"),
        ("Свободная касса", f"{_num(max(0, int(balance) - reserve_required))} см"),
        (
            "Покрытие обязательств",
            f"{(int(balance) + int(reserve)) / liability * 100:.1f}%" if liability else "100.0%",
        ),
        ("Притоки за период", f"{_num(income)} см"),
        ("Оттоки за период", f"{_num(expense)} см"),
        ("Эмиссия всего", f"{_num(emission)} см"),
        ("Bail-in всего", f"{_num(bailin)} см"),
        ("Банкротств", _num(bankruptcies)),
        ("Главный поток", reasons.most_common(1)[0][0] if reasons else "—"),
    ]
    # Convert ledger rows to event-like buckets without leaking the ORM shape.
    labels: list[str] = []
    values: list[float] = []
    grouped: dict[str, float] = defaultdict(float)
    for row in rows:
        dt = datetime.fromtimestamp(row.created_at, zone)
        key = dt.strftime("%H:00" if period == "d" else "%d.%m")
        grouped[key] += row.cash_delta
    labels = list(grouped)
    values = list(grouped.values())
    watermark = await detailed_since()
    note = f"Точный журнал потоков ведётся с {datetime.fromtimestamp(watermark, zone):%d.%m.%Y}."
    return Dashboard(
        title, "corp", period, metrics, labels, values, "Чистый поток Корпорации", "см", note
    )


async def _leaders_dashboard(
    scope: Scope,
    period: str,
    events: list[Event],
    title: str,
    zone: ZoneInfo,
    now: int,
) -> Dashboard:
    activity: Counter[int] = Counter()
    by_chat = scope.kind == "global"
    for event in events:
        key = event.chat_id if by_chat else event.user_id
        if not key:
            continue
        activity[key] += 1
    wealth_rows = await wealth.rows(chat_id=None if by_chat else scope.chat_id)
    scores: dict[int, int] = defaultdict(int)
    names: dict[int, str] = {}
    if by_chat:
        for row in wealth_rows:
            scores[row.chat_id] += row.net
        ids = set(scores)
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Chat.chat_id, Chat.title).where(Chat.chat_id.in_(ids))
                    )
                ).all()
                if ids
                else []
            )
        names.update({int(key): str(name or key) for key, name in rows})
    else:
        for row in wealth_rows:
            scores[row.user_id] = row.net
            names[row.user_id] = row.name
    ids = set(scores)
    ordered = sorted(ids, key=lambda key: (scores[key], activity[key]), reverse=True)
    best = ordered[:10]
    worst = sorted(ids, key=lambda key: scores[key])[:3]
    metrics: list[tuple[str, str]] = [
        ("Участников рейтинга", _num(len(ids))),
        ("Событий", _num(sum(activity.values()))),
    ]
    for index, key in enumerate(best, 1):
        metrics.append((f"#{index}", f"{names.get(key, str(key))}: {scores[key]} см"))
    if worst and by_chat:
        key = worst[0]
        metrics.append(("Самый нищий", f"{names.get(key, str(key))}: {scores[key]} см"))
    labels = [names.get(key, str(key))[:18] for key in best]
    values = [float(scores[key]) for key in best]
    chart_kind = "bar"
    chart_series: list[tuple[str, list[float]]] = []
    chart_title = "Чистое состояние чатов" if by_chat else "Чистое состояние игроков"
    note = ""
    if scope.kind in {"chat", "leaderboard"}:
        labels, chart_series = await _chat_leader_timeline(scope.chat_id, period, zone, now)
        values = chart_series[0][1] if chart_series else []
        chart_kind = "line"
        chart_title = "Гонка текущих лидеров"
        note = "Текущая десятка чата по наличке, вкладам и покеру за вычетом долгов."
    return Dashboard(
        title,
        "leaders",
        period,
        metrics,
        labels,
        values,
        chart_title,
        "см",
        note,
        chart_kind,
        chart_series,
    )


async def detailed_since() -> int:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(AnalyticsState, 1)
        if row is None:
            row = AnalyticsState(id=1)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return int(row.detailed_since)


def caption(data: Dashboard) -> str:
    lines = [
        f"📊 <b>{SECTIONS[data.section]}</b> · {PERIODS[data.period]}",
        f"<b>{html.escape(data.title)}</b>",
        "",
    ]
    for label, value in data.metrics:
        line = f"• {html.escape(label)}: <b>{html.escape(value)}</b>"
        if len("\n".join([*lines, line])) > 900:
            lines.append("• Остальное не влезло в подпись — жми CSV.")
            break
        lines.append(line)
    if data.note:
        note = f"ℹ️ {html.escape(data.note)}"
        if len("\n".join([*lines, "", note])) <= 1000:
            lines.extend(["", note])
    has_chart_data = bool(data.labels) and (
        any(any(math.isfinite(value) for value in values) for _name, values in data.series)
        if data.series
        else bool(data.values)
    )
    if not has_chart_data:
        lines.extend(["", "За этот период статистическая пустыня. Даже обосраться не успели."])
    return "\n".join(lines)


def _render_sync(data: Dashboard) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    if data.labels:
        if data.chart_kind == "line":
            plotted = data.series or [(data.title, data.values)]
            for name, values in plotted:
                ax.plot(
                    range(len(values)),
                    values,
                    marker="o",
                    markersize=3.5,
                    linewidth=2,
                    label=name,
                )
            if len(plotted) > 1:
                ax.legend(
                    loc="upper left",
                    bbox_to_anchor=(1.01, 1),
                    borderaxespad=0,
                    frameon=False,
                    fontsize=9,
                )
            ax.margins(x=0.02)
        else:
            colors = ["#2e8b57" if value >= 0 else "#c94c4c" for value in data.values]
            ax.bar(range(len(data.values)), data.values, color=colors, width=0.75)
            ax.axhline(0, color="#555555", linewidth=0.8)
        ax.set_xticks(range(len(data.labels)))
        shown = max(1, len(data.labels) // 12)
        ax.set_xticklabels(
            [label if index % shown == 0 else "" for index, label in enumerate(data.labels)],
            rotation=35,
            ha="right",
        )
    else:
        ax.text(0.5, 0.5, "Нет данных за выбранный период", ha="center", va="center", fontsize=18)
        ax.set_xticks([])
        ax.set_yticks([])
    ax.set_title(data.chart_title, fontsize=18, pad=16)
    ax.set_ylabel(data.chart_ylabel)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format="png", metadata={"Software": "noadick analytics"})
    plt.close(fig)
    return out.getvalue()


async def render_png(data: Dashboard) -> bytes:
    key = (
        data.title,
        data.section,
        data.period,
        tuple(data.metrics),
        tuple(data.labels),
        tuple(data.values),
        tuple((name, tuple(values)) for name, values in data.series),
        data.chart_title,
        data.chart_ylabel,
        data.chart_kind,
    )
    now = time.monotonic()
    cached = _png_cache.get(key)
    if cached is not None and now - cached[0] <= _PNG_CACHE_TTL:
        return cached[1]
    async with _render_slots:
        rendered = await asyncio.to_thread(_render_sync, data)
    if len(_png_cache) >= _PNG_CACHE_MAX:
        oldest = min(_png_cache, key=lambda item: _png_cache[item][0])
        _png_cache.pop(oldest, None)
    _png_cache[key] = (now, rendered)
    return rendered


def render_csv(data: Dashboard) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["раздел", SECTIONS[data.section]])
    writer.writerow(["период", PERIODS[data.period]])
    writer.writerow(["объект", data.title])
    for label, value in data.metrics:
        writer.writerow([label, value])
    writer.writerow([])
    if data.series:
        writer.writerow(["интервал", *(name for name, _values in data.series)])
        for index, label in enumerate(data.labels):
            writer.writerow(
                [
                    label,
                    *(
                        "" if math.isnan(values[index]) else values[index]
                        for _name, values in data.series
                    ),
                ]
            )
        writer.writerow(
            [
                "ПОСЛЕДНЕЕ",
                *(
                    next((value for value in reversed(values) if not math.isnan(value)), "")
                    for _name, values in data.series
                ),
            ]
        )
    else:
        writer.writerow(["интервал", "значение"])
        writer.writerows(zip(data.labels, data.values, strict=True))
        writer.writerow(["ИТОГО", sum(data.values)])
    return ("\ufeff" + out.getvalue()).encode("utf-8")
