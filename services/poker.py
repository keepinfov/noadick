"""Poker room application service.

This layer serialises per-table state transitions and delegates bankroll
transfers to the repository.  Telegram rendering lives in ``handlers.poker``.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from db.models import PokerHand, PokerSeat, PokerTable
from models import poker as engine
from repositories import poker as repo
from repositories.players import get_chat_lock
from services.global_settings import get_config_sync

MIN_SEATS = 2
MAX_SEATS = 6
MIN_TIMEOUT = 30
MAX_TIMEOUT = 180
MAX_CHIPS = 1_000_000_000
HOST_IDLE_SECONDS = 30 * 60

PRESETS: dict[str, tuple[int, int, int]] = {
    "quick": (20, 1, 2),
    "normal": (40, 1, 2),
    "deep": (100, 1, 2),
}

_locks: dict[str, asyncio.Lock] = {}


def table_lock(table_id: str) -> asyncio.Lock:
    lock = _locks.get(table_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[table_id] = lock
    return lock


def new_table_id() -> str:
    # Upper-case token stays readable in Telegram while retaining enough space
    # for concurrent rooms.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def validate_config(
    buy_in: int, small_blind: int, big_blind: int, max_seats: int, timeout: int
) -> None:
    if not MIN_SEATS <= max_seats <= MAX_SEATS:
        raise repo.PokerRepoError("bad_seats")
    if small_blind < 1 or big_blind < small_blind:
        raise repo.PokerRepoError("bad_blinds")
    if buy_in < big_blind * 10:
        raise repo.PokerRepoError("buyin_too_small")
    if buy_in > MAX_CHIPS or big_blind > MAX_CHIPS or small_blind > MAX_CHIPS:
        raise repo.PokerRepoError("amount_too_large")
    if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
        raise repo.PokerRepoError("bad_timeout")


async def create_table(
    *,
    mode: str,
    chat_id: int | None,
    thread_id: int | None,
    host_id: int,
    host_name: str,
    access_mode: str = "approval",
    buy_in: int,
    small_blind: int,
    big_blind: int,
    max_seats: int,
    timeout: int,
) -> PokerTable:
    validate_config(buy_in, small_blind, big_blind, max_seats, timeout)
    if mode not in {"money", "practice"} or access_mode not in {
        "open",
        "approval",
        "invite",
    }:
        raise repo.PokerRepoError("bad_config")
    table_id = new_table_id()
    kwargs = dict(
        table_id=table_id,
        mode=mode,
        chat_id=chat_id,
        thread_id=thread_id,
        host_id=host_id,
        host_name=host_name,
        access_mode=access_mode,
        max_seats=max_seats,
        buy_in=buy_in,
        small_blind=small_blind,
        big_blind=big_blind,
        turn_timeout=timeout,
    )
    if mode == "money":
        assert chat_id is not None
        async with get_chat_lock(chat_id):
            return await repo.create_table_with_host(**kwargs)
    return await repo.create_table_with_host(**kwargs)


async def ask_to_join(table_id: str, user_id: int, name: str) -> str:
    """Return ``joined``, ``requested`` or ``already``."""
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None or table.status == "closed":
            raise repo.PokerRepoError("table_closed")
        existing = await repo.get_active_seat(user_id)
        if existing is not None:
            if existing.table_id == table_id:
                return "already"
            raise repo.PokerRepoError("already_seated")
        request = await repo.get_join_request(table_id, user_id)
        if request is not None and request.status == "banned":
            raise repo.PokerRepoError("table_banned")
        allowed = request is not None and (
            request.status == "allowed"
            or (request.status == "approved" and request.expires_at >= int(time.time()))
        )
        if table.access_mode == "open" or allowed:
            if table.mode == "money":
                assert table.chat_id is not None
                async with get_chat_lock(table.chat_id):
                    await repo.join_table(table_id, user_id, name)
            else:
                await repo.join_table(table_id, user_id, name)
            return "joined"
        if table.access_mode == "invite":
            raise repo.PokerRepoError("invite_only")
        await repo.create_join_request(table_id, user_id, name)
        return "requested"


async def decide_request(
    table_id: str, host_id: int, user_id: int, *, approve: bool, remember: bool = False
) -> str:
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None or table.host_id != host_id:
            raise repo.PokerRepoError("host_only")
        request = await repo.get_join_request(table_id, user_id)
        if request is None or request.status != "pending" or request.expires_at < int(time.time()):
            raise repo.PokerRepoError("request_expired")
        if not approve:
            await repo.set_request_status(table_id, user_id, "banned" if remember else "denied")
            return "denied"
        await repo.set_request_status(table_id, user_id, "allowed" if remember else "approved")
        return "approved"


async def join_with_invite(token: str, user_id: int, name: str) -> str:
    # Consuming first makes a forwarded one-use token deterministic. If the
    # actual join fails, the host can generate another token.
    table_id = await repo.consume_invite(token, user_id)
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None:
            raise repo.PokerRepoError("table_missing")
        if table.mode == "money":
            assert table.chat_id is not None
            async with get_chat_lock(table.chat_id):
                await repo.join_table(table_id, user_id, name)
        else:
            await repo.join_table(table_id, user_id, name)
    return table_id


async def toggle_ready(table_id: str, user_id: int) -> tuple[bool, PokerHand | None]:
    async with table_lock(table_id):
        seat = await repo.get_seat(table_id, user_id)
        if seat is None or seat.status != "active":
            raise repo.PokerRepoError("not_seated")
        new_value = not seat.ready
        await repo.set_ready(table_id, user_id, new_value)
        if new_value:
            hand = await _start_if_possible(table_id, host_id=None, force=False)
            return new_value, hand
        return new_value, None


async def start_with_ready(table_id: str, host_id: int) -> PokerHand:
    async with table_lock(table_id):
        hand = await _start_if_possible(table_id, host_id=host_id, force=True)
        if hand is None:
            raise repo.PokerRepoError("not_everyone_ready")
        return hand


async def _start_if_possible(
    table_id: str, *, host_id: int | None, force: bool
) -> PokerHand | None:
    table = await repo.get_table(table_id)
    if table is None or table.status == "closed":
        raise repo.PokerRepoError("table_closed")
    if table.paused or table.status == "playing":
        return None
    if force and table.host_id != host_id:
        raise repo.PokerRepoError("host_only")
    seats = await repo.list_seats(table_id)
    eligible = [seat for seat in seats if seat.stack > 0]
    ready = [seat for seat in eligible if seat.ready]
    if len(ready) < 2:
        return None
    if not force and len(ready) != len(eligible):
        return None
    state = engine.start_hand(
        [
            {
                "user_id": seat.user_id,
                "seat": seat.seat_no,
                "name": seat.name,
                "stack": seat.stack,
            }
            for seat in ready
        ],
        small_blind=table.small_blind,
        big_blind=table.big_blind,
        dealer_seat=table.dealer_seat,
    )
    hand = await repo.create_hand(table_id, state, [seat.user_id for seat in ready])
    if state["finished"]:
        hand = await repo.save_hand(table_id, hand.hand_id, state, expected_version=hand.version)
    return hand


@dataclass
class ActionResult:
    table: PokerTable
    hand: PokerHand
    actor_id: int
    finished: bool


async def act(
    table_id: str,
    user_id: int,
    action: str,
    *,
    version: int,
    amount: int | None = None,
    timed_out: bool = False,
) -> ActionResult:
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        hand = await repo.get_active_hand(table_id)
        if table is None or hand is None:
            raise repo.PokerRepoError("hand_missing")
        if hand.version != version:
            raise repo.PokerRepoError("stale_action")
        state = dict(hand.state)
        street_before = str(state["street"])
        engine.apply_action(state, user_id, action, amount)
        if timed_out:
            name = state["players"][str(user_id)]["name"]
            if action == "check":
                state["last_action"] = (
                    f"{name} не нашёл мозг до сигнала. Бот чекнул за это сонное туловище."
                )
            else:
                state["last_action"] = (
                    f"{name} просидел весь таймер с пальцем в жопе и был выброшен в автофолд."
                )
        saved = await repo.save_hand(
            table_id,
            hand.hand_id,
            state,
            expected_version=version,
            timed_out_uid=user_id if timed_out else None,
            garnish_pct=get_config_sync().loan_duel_garnish_pct,
            action=action,
            actor_id=user_id,
            action_amount=amount,
            action_street=street_before,
        )
        if not timed_out:
            await repo.reset_timeout_counter(table_id, user_id)
        refreshed_table = await repo.get_table(table_id)
        assert refreshed_table is not None
        return ActionResult(refreshed_table, saved, user_id, bool(state["finished"]))


async def top_up(table_id: str, user_id: int, amount: int) -> PokerSeat:
    if amount > MAX_CHIPS:
        raise repo.PokerRepoError("amount_too_large")
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None:
            raise repo.PokerRepoError("table_missing")
        if table.mode == "money":
            assert table.chat_id is not None
            async with get_chat_lock(table.chat_id):
                return await repo.top_up(table_id, user_id, amount)
        return await repo.top_up(table_id, user_id, amount)


async def leave(table_id: str, user_id: int) -> str:
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None:
            raise repo.PokerRepoError("table_missing")
        if table.mode == "money":
            assert table.chat_id is not None
            async with get_chat_lock(table.chat_id):
                result = await repo.mark_departure(
                    table_id,
                    user_id,
                    garnish_pct=get_config_sync().loan_duel_garnish_pct,
                )
                if result == "close_now":
                    await repo.close_table(
                        table_id,
                        reason="host_left",
                        garnish_pct=get_config_sync().loan_duel_garnish_pct,
                    )
                return result
        result = await repo.mark_departure(table_id, user_id)
        if result == "close_now":
            await repo.close_table(table_id, reason="host_left")
        return result


async def kick(table_id: str, host_id: int, user_id: int) -> str:
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None or table.host_id != host_id:
            raise repo.PokerRepoError("host_only")
        if user_id == host_id:
            raise repo.PokerRepoError("cannot_kick_host")
        if table.mode == "money":
            assert table.chat_id is not None
            async with get_chat_lock(table.chat_id):
                return await repo.mark_departure(
                    table_id,
                    user_id,
                    kick=True,
                    garnish_pct=get_config_sync().loan_duel_garnish_pct,
                )
        return await repo.mark_departure(table_id, user_id, kick=True)


async def close(table_id: str, host_id: int, *, reason: str = "host_closed") -> dict[int, int]:
    async with table_lock(table_id):
        table = await repo.get_table(table_id)
        if table is None or table.host_id != host_id:
            raise repo.PokerRepoError("host_only")
        if table.mode == "money":
            assert table.chat_id is not None
            async with get_chat_lock(table.chat_id):
                return await repo.close_table(
                    table_id,
                    reason=reason,
                    garnish_pct=get_config_sync().loan_duel_garnish_pct,
                )
        return await repo.close_table(table_id, reason=reason)


async def process_due_actions() -> set[str]:
    """Apply all expired turn/host timers and return changed table IDs."""
    changed: set[str] = set()
    now = int(time.time())
    for table in await repo.list_active_tables():
        if table.status != "playing":
            continue
        hand = await repo.get_active_hand(table.table_id)
        if hand is None or hand.deadline is None or hand.deadline > now:
            continue
        uid = hand.state.get("current_uid")
        if uid is None:
            continue
        legal = engine.legal_actions(hand.state, uid)
        action = "check" if legal.get("check") else "fold"
        try:
            await act(
                table.table_id,
                int(uid),
                action,
                version=hand.version,
                timed_out=True,
            )
            changed.add(table.table_id)
        except (repo.PokerRepoError, engine.PokerError):
            # A concurrent callback won the race; its renderer will refresh.
            continue

    for table in await repo.idle_host_tables(now - HOST_IDLE_SECONDS):
        async with table_lock(table.table_id):
            latest = await repo.get_table(table.table_id)
            if latest is None or latest.status not in {"lobby", "between"}:
                continue
            if latest.mode == "money" and latest.chat_id is not None:
                async with get_chat_lock(latest.chat_id):
                    await repo.close_table(
                        latest.table_id,
                        reason="host_idle",
                        garnish_pct=get_config_sync().loan_duel_garnish_pct,
                    )
            else:
                await repo.close_table(latest.table_id, reason="host_idle")
            changed.add(latest.table_id)
    return changed
