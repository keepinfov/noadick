"""Profile statistics computed from the event log.

Telegram-agnostic so the same functions back /me today and chart rendering /
a web panel later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from repositories import chats as C
from repositories import events as E
from repositories import players as P
from services import settings as chat_settings
from services import wealth

BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[int | float]) -> str:
    vals = list(values)
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return BLOCKS[len(BLOCKS) // 2] * len(vals)
    span = hi - lo
    out = []
    for v in vals:
        idx = int((v - lo) / span * (len(BLOCKS) - 1))
        out.append(BLOCKS[idx])
    return "".join(out)


@dataclass
class ProfileStats:
    name: str
    current_size: int
    rank: int
    net_worth: int
    net_rank: int
    plays: int
    days_played: int
    total_grown: int
    total_lost: int
    best_day: int | None
    worst_day: int | None
    duels_total: int
    wins: int
    losses: int
    winrate: float
    stolen_total: int
    lost_in_duels: int
    diseases_caught: int
    current_disease: str | None
    exists: bool


async def compute_profile(chat_id: int, user_id: int) -> ProfileStats:
    player = await P.get_player(chat_id, user_id)
    rank = await P.get_rank(chat_id, user_id)
    wealth_rows = await wealth.chat_rows(chat_id)
    wealth_entry = next((row for row in wealth_rows if row.user_id == user_id), None)
    net_rank = next(
        (index for index, row in enumerate(wealth_rows, 1) if row.user_id == user_id),
        len(wealth_rows) + 1,
    )
    evs = await E.get_events(chat_id, user_id)
    tz = await chat_settings.resolve_tz(chat_id)

    plays = 0
    total_grown = 0
    total_lost = 0
    best: int | None = None
    worst: int | None = None
    days: set[date] = set()
    wins = 0
    losses = 0
    stolen = 0
    lost_duel = 0
    diseases = 0

    for e in evs:
        if e.type == E.DICK:
            plays += 1
            d = e.delta
            if d >= 0:
                total_grown += d
            else:
                total_lost += -d
            best = d if best is None else max(best, d)
            worst = d if worst is None else min(worst, d)
            days.add(datetime.fromtimestamp(e.created_at, tz).date())
        elif e.type == E.DUEL:
            won = (e.meta or {}).get("won")
            if won:
                wins += 1
                stolen += max(0, e.delta)
            else:
                losses += 1
                lost_duel += max(0, -e.delta)
        elif e.type == E.INFECTION:
            diseases += 1

    duels_total = wins + losses
    return ProfileStats(
        name=player.name if player else str(user_id),
        current_size=player.size if player else 0,
        rank=rank,
        net_worth=wealth_entry.net if wealth_entry else (player.size if player else 0),
        net_rank=net_rank,
        plays=plays,
        days_played=len(days),
        total_grown=total_grown,
        total_lost=total_lost,
        best_day=best,
        worst_day=worst,
        duels_total=duels_total,
        wins=wins,
        losses=losses,
        winrate=(wins / duels_total) if duels_total else 0.0,
        stolen_total=stolen,
        lost_in_duels=lost_duel,
        diseases_caught=diseases,
        current_disease=player.disease_id if player else None,
        exists=player is not None,
    )


@dataclass
class GlobalChatEntry:
    chat_id: int
    title: str
    size: int
    rank: int
    net_worth: int
    net_rank: int


@dataclass
class GlobalProfileStats:
    name: str
    chats: list[GlobalChatEntry]
    chats_count: int
    plays: int
    total_grown: int
    total_lost: int
    best_roll: int | None
    worst_roll: int | None
    duels_total: int
    wins: int
    losses: int
    winrate: float
    infections: int
    best_size_ever: int
    first_play_ts: int | None
    is_banned: bool
    ban_reason: str | None
    ban_until: int | None
    exists: bool


async def compute_global_profile(user_id: int, name: str | None = None) -> GlobalProfileStats:
    sizes = await P.user_chat_sizes(user_id)
    chats: list[GlobalChatEntry] = []
    wealth_by_chat: dict[int, list[wealth.Wealth]] = {}
    for entry in await wealth.rows(chat_ids=[chat_id for chat_id, _title, _size in sizes]):
        wealth_by_chat.setdefault(entry.chat_id, []).append(entry)
    for entries in wealth_by_chat.values():
        entries.sort(key=lambda entry: (-entry.net, entry.user_id))
    for chat_id, title, size in sizes:
        rank = await P.global_rank_for(user_id, chat_id, size)
        wealth_rows = wealth_by_chat.get(chat_id, [])
        wealth_entry = next((row for row in wealth_rows if row.user_id == user_id), None)
        net_rank = next(
            (index for index, row in enumerate(wealth_rows, 1) if row.user_id == user_id),
            len(wealth_rows) + 1,
        )
        chats.append(
            GlobalChatEntry(
                chat_id=chat_id,
                title=title or str(chat_id),
                size=size,
                rank=rank,
                net_worth=wealth_entry.net if wealth_entry else size,
                net_rank=net_rank,
            )
        )

    plays, grown, lost, best, worst = await E.global_dick_aggregate(user_id)
    counts = await E.global_count_by_type(user_id)
    infections = counts.get(E.INFECTION, 0)
    best_size = await E.global_best_size(user_id)
    first_play = await E.global_first_play(user_id)

    wins = 0
    losses = 0
    for e in await E.global_duel_events(user_id):
        if bool((e.meta or {}).get("won")):
            wins += 1
        else:
            losses += 1
    duels_total = wins + losses

    user = await C.get_user(user_id)
    is_banned = bool(user and user.is_banned)
    ban_reason = user.notes if user else None
    ban_until = user.ban_until if user else None

    exists = bool(chats) or plays > 0 or duels_total > 0
    display_name = name or (user.first_name if user else None) or str(user_id)

    return GlobalProfileStats(
        name=display_name,
        chats=chats,
        chats_count=len(chats),
        plays=plays,
        total_grown=grown,
        total_lost=lost,
        best_roll=best,
        worst_roll=worst,
        duels_total=duels_total,
        wins=wins,
        losses=losses,
        winrate=(wins / duels_total) if duels_total else 0.0,
        infections=infections,
        best_size_ever=best_size,
        first_play_ts=first_play,
        is_banned=is_banned,
        ban_reason=ban_reason if is_banned else None,
        ban_until=ban_until if is_banned else None,
        exists=exists,
    )


async def size_timeline(chat_id: int, user_id: int) -> list[tuple[int, int]]:
    # Banking bookkeeping events without a liquid-size snapshot historically
    # stored the default size_after=0. Restrict the chart to events that really
    # carry a post-operation liquid balance, including the health reform.
    evs = await E.get_events(
        chat_id,
        user_id,
        types=[
            E.BASELINE,
            E.DICK,
            E.DUEL,
            E.ADMIN_ADJUST,
            E.DEPOSIT_OPEN,
            E.DEPOSIT_WITHDRAW,
            E.LOAN_OPEN,
            E.LOAN_REPAY,
            E.LOAN_GARNISH,
            E.HEALTH_REFORM,
            E.POKER_BUYIN,
            E.POKER_TOPUP,
            E.POKER_CASHOUT,
        ],
    )
    return [(e.created_at, e.size_after) for e in evs]


async def daily_deltas(chat_id: int, user_id: int, days: int = 14) -> list[tuple[date, int]]:
    evs = await E.get_events(chat_id, user_id, types=[E.DICK])
    tz = await chat_settings.resolve_tz(chat_id)
    by_date: dict[date, int] = {}
    for e in evs:
        d = datetime.fromtimestamp(e.created_at, tz).date()
        by_date[d] = by_date.get(d, 0) + e.delta
    return sorted(by_date.items())[-days:]
