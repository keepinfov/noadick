"""Profile statistics computed from the event log.

Telegram-agnostic so the same functions back /me today and chart rendering /
a web panel later.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from repositories import events as E
from repositories import players as P

BLOCKS = "▁▂▃▄▅▆▇█"


def _tz() -> ZoneInfo:
    return ZoneInfo(key=os.environ.get("TZ", "Europe/Moscow"))


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
    evs = await E.get_events(chat_id, user_id)
    tz = _tz()

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


async def size_timeline(chat_id: int, user_id: int) -> list[tuple[int, int]]:
    evs = await E.get_events(chat_id, user_id)
    return [(e.created_at, e.size_after) for e in evs]


async def daily_deltas(
    chat_id: int, user_id: int, days: int = 14
) -> list[tuple[date, int]]:
    evs = await E.get_events(chat_id, user_id, types=[E.DICK])
    tz = _tz()
    by_date: dict[date, int] = {}
    for e in evs:
        d = datetime.fromtimestamp(e.created_at, tz).date()
        by_date[d] = by_date.get(d, 0) + e.delta
    return sorted(by_date.items())[-days:]
