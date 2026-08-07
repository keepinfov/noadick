"""Persistence and atomic bankroll operations for poker rooms."""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from db.engine import get_session_factory
from db.models import (
    Corporation,
    Event,
    Loan,
    Player,
    PokerHand,
    PokerInvite,
    PokerJoinRequest,
    PokerSeat,
    PokerTable,
)
from repositories import events as E


class PokerRepoError(ValueError):
    pass


def _now() -> int:
    return int(time.time())


def _event(
    chat_id: int,
    user_id: int,
    kind: str,
    delta: int,
    size_after: int,
    meta: dict,
) -> Event:
    return Event(
        chat_id=chat_id,
        user_id=user_id,
        type=kind,
        delta=delta,
        size_after=size_after,
        meta=meta,
        created_at=_now(),
    )


async def get_table(table_id: str) -> PokerTable | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(PokerTable, table_id)


async def list_location_tables(chat_id: int, thread_id: int | None) -> list[PokerTable]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(PokerTable)
            .where(
                PokerTable.chat_id == chat_id,
                PokerTable.status != "closed",
            )
            .order_by(PokerTable.created_at)
        )
        if thread_id is None:
            stmt = stmt.where(PokerTable.thread_id.is_(None))
        else:
            stmt = stmt.where(PokerTable.thread_id == thread_id)
        return list((await session.execute(stmt)).scalars().all())


async def list_active_tables() -> list[PokerTable]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (await session.execute(select(PokerTable).where(PokerTable.status != "closed")))
            .scalars()
            .all()
        )


async def get_active_seat(user_id: int) -> PokerSeat | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(PokerSeat).where(
                    PokerSeat.user_id == user_id,
                    PokerSeat.status == "active",
                )
            )
        ).scalar_one_or_none()


async def get_money_stack(chat_id: int, user_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        value = (
            await session.execute(
                select(PokerSeat.stack + PokerSeat.committed)
                .join(PokerTable, PokerTable.table_id == PokerSeat.table_id)
                .where(
                    PokerSeat.user_id == user_id,
                    PokerSeat.status == "active",
                    PokerTable.chat_id == chat_id,
                    PokerTable.mode == "money",
                    PokerTable.status != "closed",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return int(value or 0)


async def get_seat(table_id: str, user_id: int) -> PokerSeat | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(PokerSeat, (table_id, user_id))


async def list_seats(table_id: str, *, active_only: bool = True) -> list[PokerSeat]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(PokerSeat).where(PokerSeat.table_id == table_id)
        if active_only:
            stmt = stmt.where(PokerSeat.status == "active")
        stmt = stmt.order_by(PokerSeat.seat_no)
        return list((await session.execute(stmt)).scalars().all())


async def create_table_with_host(
    *,
    table_id: str,
    mode: str,
    chat_id: int | None,
    thread_id: int | None,
    host_id: int,
    host_name: str,
    access_mode: str,
    max_seats: int,
    buy_in: int,
    small_blind: int,
    big_blind: int,
    turn_timeout: int,
) -> PokerTable:
    factory = get_session_factory()
    async with factory() as session:
        occupied = (
            await session.execute(
                select(PokerSeat.table_id).where(
                    PokerSeat.user_id == host_id, PokerSeat.status == "active"
                )
            )
        ).scalar_one_or_none()
        if occupied is not None:
            raise PokerRepoError("already_seated")

        if mode == "money":
            if chat_id is None:
                raise PokerRepoError("money_needs_chat")
            player = await session.get(Player, (chat_id, host_id))
            if player is None:
                raise PokerRepoError("not_a_player")
            if player.is_chat_banned:
                raise PokerRepoError("locally_banned")
            if player.size < buy_in:
                raise PokerRepoError("not_enough")
            player.size -= buy_in
            session.add(
                _event(
                    chat_id,
                    host_id,
                    E.POKER_BUYIN,
                    -buy_in,
                    player.size,
                    {"table_id": table_id, "amount": buy_in},
                )
            )

        table = PokerTable(
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
            turn_timeout=turn_timeout,
            status="lobby",
            last_event="Стол открыт. Мораль закрыта.",
        )
        session.add(table)
        session.add(
            PokerSeat(
                table_id=table_id,
                user_id=host_id,
                seat_no=1,
                name=host_name,
                stack=buy_in,
                total_buyin=buy_in,
            )
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise PokerRepoError("already_seated") from exc
        return table


async def join_table(table_id: str, user_id: int, name: str) -> PokerSeat:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        if table is None or table.status == "closed" or table.close_after_hand:
            raise PokerRepoError("table_closed")
        occupied = (
            await session.execute(
                select(PokerSeat.table_id).where(
                    PokerSeat.user_id == user_id, PokerSeat.status == "active"
                )
            )
        ).scalar_one_or_none()
        if occupied is not None:
            if occupied == table_id:
                seat = await session.get(PokerSeat, (table_id, user_id))
                assert seat is not None
                return seat
            raise PokerRepoError("already_seated")
        seats = list(
            (
                await session.execute(
                    select(PokerSeat).where(
                        PokerSeat.table_id == table_id, PokerSeat.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(seats) >= table.max_seats:
            raise PokerRepoError("table_full")
        used = {seat.seat_no for seat in seats}
        seat_no = next(number for number in range(1, table.max_seats + 1) if number not in used)

        if table.mode == "money":
            assert table.chat_id is not None
            player = await session.get(Player, (table.chat_id, user_id))
            if player is None:
                raise PokerRepoError("not_a_player")
            if player.is_chat_banned:
                raise PokerRepoError("locally_banned")
            if player.size < table.buy_in:
                raise PokerRepoError("not_enough")
            player.size -= table.buy_in
            session.add(
                _event(
                    table.chat_id,
                    user_id,
                    E.POKER_BUYIN,
                    -table.buy_in,
                    player.size,
                    {"table_id": table_id, "amount": table.buy_in},
                )
            )

        old = await session.get(PokerSeat, (table_id, user_id))
        if old is None:
            seat = PokerSeat(
                table_id=table_id,
                user_id=user_id,
                seat_no=seat_no,
                name=name,
                stack=table.buy_in,
                total_buyin=table.buy_in,
            )
            session.add(seat)
        else:
            seat = old
            seat.seat_no = seat_no
            seat.name = name
            seat.stack = table.buy_in
            seat.committed = 0
            seat.total_buyin += table.buy_in
            seat.ready = False
            seat.sitting_out = False
            seat.pending_leave = False
            seat.pending_kick = False
            seat.consecutive_timeouts = 0
            seat.status = "active"
            seat.left_at = None
        request = await session.get(PokerJoinRequest, (table_id, user_id))
        if request is not None and request.status != "allowed":
            request.status = "joined"
        table.last_event = f"{name} принёс за стол {table.buy_in} см."
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise PokerRepoError("already_seated") from exc
        return seat


async def create_join_request(table_id: str, user_id: int, name: str) -> PokerJoinRequest:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        if table is None or table.status == "closed":
            raise PokerRepoError("table_closed")
        row = await session.get(PokerJoinRequest, (table_id, user_id))
        if row is not None and row.status == "banned":
            raise PokerRepoError("table_banned")
        expiry = _now() + 120
        if row is None:
            row = PokerJoinRequest(
                table_id=table_id,
                user_id=user_id,
                name=name,
                status="pending",
                expires_at=expiry,
            )
            session.add(row)
        elif row.status != "allowed":
            row.name = name
            row.status = "pending"
            row.expires_at = expiry
        await session.commit()
        return row


async def get_join_request(table_id: str, user_id: int) -> PokerJoinRequest | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(PokerJoinRequest, (table_id, user_id))


async def set_request_status(table_id: str, user_id: int, status: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(PokerJoinRequest, (table_id, user_id))
        if row is None:
            raise PokerRepoError("request_missing")
        row.status = status
        row.updated_at = _now()
        await session.commit()


async def allow_user(table_id: str, user_id: int, name: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(PokerJoinRequest, (table_id, user_id))
        if row is None:
            row = PokerJoinRequest(
                table_id=table_id,
                user_id=user_id,
                name=name,
                status="allowed",
                expires_at=2_147_483_647,
            )
            session.add(row)
        else:
            row.name = name
            row.status = "allowed"
            row.expires_at = 2_147_483_647
        await session.commit()


async def list_requests(table_id: str) -> list[PokerJoinRequest]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PokerJoinRequest)
                    .where(
                        PokerJoinRequest.table_id == table_id,
                        PokerJoinRequest.status == "pending",
                        PokerJoinRequest.expires_at >= _now(),
                    )
                    .order_by(PokerJoinRequest.created_at)
                )
            )
            .scalars()
            .all()
        )


async def list_access_entries(table_id: str, status: str) -> list[PokerJoinRequest]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PokerJoinRequest)
                    .where(
                        PokerJoinRequest.table_id == table_id,
                        PokerJoinRequest.status == status,
                    )
                    .order_by(PokerJoinRequest.name)
                )
            )
            .scalars()
            .all()
        )


async def create_invite(
    table_id: str, *, user_id: int | None = None, uses: int = 1, ttl: int = 86400
) -> PokerInvite:
    token = secrets.token_urlsafe(12).replace("-", "").replace("_", "")
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        if table is None or table.status == "closed":
            raise PokerRepoError("table_closed")
        row = PokerInvite(
            token=token,
            table_id=table_id,
            user_id=user_id,
            remaining_uses=max(1, uses),
            expires_at=_now() + ttl,
        )
        session.add(row)
        await session.commit()
        return row


async def consume_invite(token: str, user_id: int) -> str:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(PokerInvite, token)
        if (
            row is None
            or row.expires_at < _now()
            or row.remaining_uses <= 0
            or (row.user_id is not None and row.user_id != user_id)
        ):
            raise PokerRepoError("invite_invalid")
        row.remaining_uses -= 1
        await session.commit()
        return row.table_id


async def top_up(table_id: str, user_id: int, amount: int) -> PokerSeat:
    if amount < 1:
        raise PokerRepoError("bad_amount")
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        seat = await session.get(PokerSeat, (table_id, user_id))
        if table is None or seat is None or seat.status != "active":
            raise PokerRepoError("not_seated")
        if table.status == "playing":
            raise PokerRepoError("hand_active")
        if table.mode == "money":
            assert table.chat_id is not None
            player = await session.get(Player, (table.chat_id, user_id))
            if player is None or player.size < amount:
                raise PokerRepoError("not_enough")
            player.size -= amount
            session.add(
                _event(
                    table.chat_id,
                    user_id,
                    E.POKER_TOPUP,
                    -amount,
                    player.size,
                    {"table_id": table_id, "amount": amount},
                )
            )
        seat.stack += amount
        seat.total_buyin += amount
        await session.commit()
        return seat


async def set_ready(table_id: str, user_id: int, ready: bool) -> None:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        seat = await session.get(PokerSeat, (table_id, user_id))
        if table is None or seat is None or seat.status != "active":
            raise PokerRepoError("not_seated")
        if table.status == "playing":
            raise PokerRepoError("hand_active")
        if ready and seat.stack <= 0:
            raise PokerRepoError("empty_stack")
        seat.ready = ready
        seat.sitting_out = not ready
        if user_id == table.host_id:
            table.host_active_at = _now()
        await session.commit()


async def mark_departure(
    table_id: str, user_id: int, *, kick: bool = False, garnish_pct: int = 0
) -> str:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        seat = await session.get(PokerSeat, (table_id, user_id))
        if table is None or seat is None or seat.status != "active":
            raise PokerRepoError("not_seated")
        if user_id == table.host_id:
            if table.status == "playing":
                table.close_after_hand = True
                seat.pending_leave = True
                await session.commit()
                return "after_hand"
            # Caller performs a full close so every stack is returned.
            return "close_now"
        if table.status == "playing":
            if kick:
                seat.pending_kick = True
            else:
                seat.pending_leave = True
            await session.commit()
            return "after_hand"
        await _cashout_in_session(
            session,
            table,
            seat,
            reason="kick" if kick else "leave",
            garnish_pct=garnish_pct,
        )
        await session.commit()
        return "left"


async def _cashout_in_session(
    session,
    table: PokerTable,
    seat: PokerSeat,
    *,
    reason: str,
    garnish_pct: int = 0,
) -> int:
    returned = int(seat.stack)
    if table.mode == "money" and table.chat_id is not None:
        player = await session.get(Player, (table.chat_id, seat.user_id))
        if player is not None:
            garnished = 0
            profit = max(0, returned - int(seat.total_buyin))
            loan = await session.get(Loan, (table.chat_id, seat.user_id))
            if loan is not None and loan.defaulted and profit > 0 and garnish_pct > 0:
                debt = int(loan.principal + loan.accrued_interest)
                garnished = min(debt, returned, (profit * garnish_pct + 99) // 100)
                interest_part = min(garnished, int(loan.accrued_interest))
                principal_part = garnished - interest_part
                if garnished >= debt:
                    await session.delete(loan)
                else:
                    loan.accrued_interest -= interest_part
                    loan.principal -= principal_part
                    loan.roll_debt_principal = max(0, loan.roll_debt_principal - principal_part)
                corp = await session.get(Corporation, 1)
                if corp is None:
                    session.add(Corporation(id=1))
                    await session.flush()
                await session.execute(
                    update(Corporation)
                    .where(Corporation.id == 1)
                    .values(
                        balance=Corporation.balance + garnished,
                        total_interest_earned=Corporation.total_interest_earned + interest_part,
                    )
                )
            credited = returned - garnished
            player.size += credited
            session.add(
                _event(
                    table.chat_id,
                    seat.user_id,
                    E.POKER_CASHOUT,
                    credited,
                    player.size,
                    {
                        "table_id": table.table_id,
                        "amount": credited,
                        "net": credited - seat.total_buyin,
                        "garnished": garnished,
                        "reason": reason,
                    },
                )
            )
            if garnished:
                session.add(
                    _event(
                        table.chat_id,
                        seat.user_id,
                        E.LOAN_GARNISH,
                        0,
                        player.size,
                        {
                            "table_id": table.table_id,
                            "cleared": loan is None or garnished >= debt,
                            "from_poker": True,
                        },
                    )
                )
    seat.stack = 0
    seat.committed = 0
    seat.ready = False
    seat.sitting_out = True
    seat.status = "left"
    seat.left_at = _now()
    return returned


async def close_table(table_id: str, *, reason: str, garnish_pct: int = 0) -> dict[int, int]:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        if table is None:
            raise PokerRepoError("table_missing")
        if table.status == "playing":
            table.close_after_hand = True
            await session.commit()
            return {}
        seats = list(
            (
                await session.execute(
                    select(PokerSeat).where(
                        PokerSeat.table_id == table_id, PokerSeat.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        returned: dict[int, int] = {}
        for seat in seats:
            returned[seat.user_id] = await _cashout_in_session(
                session, table, seat, reason=reason, garnish_pct=garnish_pct
            )
        table.status = "closed"
        table.close_after_hand = False
        table.last_event = "Стол закрыт, остатки распиханы по владельцам."
        await session.commit()
        return returned


async def create_hand(table_id: str, state: dict, participants: Iterable[int]) -> PokerHand:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        if table is None or table.status == "closed":
            raise PokerRepoError("table_closed")
        if table.status == "playing":
            raise PokerRepoError("hand_active")
        participant_set = {int(uid) for uid in participants}
        seats = list(
            (
                await session.execute(
                    select(PokerSeat).where(
                        PokerSeat.table_id == table_id, PokerSeat.status == "active"
                    )
                )
            )
            .scalars()
            .all()
        )
        for seat in seats:
            if seat.user_id in participant_set:
                seat.stack = int(state["players"][str(seat.user_id)]["stack"])
                seat.committed = int(state["players"][str(seat.user_id)]["total_bet"])
            else:
                seat.committed = 0
            seat.ready = False
            seat.sitting_out = seat.user_id not in participant_set
        table.hand_no += 1
        table.status = "playing"
        table.settings_locked = True
        table.dealer_seat = int(state["dealer_seat"])
        table.host_active_at = _now()
        hand = PokerHand(
            table_id=table_id,
            hand_no=table.hand_no,
            status="active",
            street=state["street"],
            state=state,
            deadline=_now() + table.turn_timeout if state["current_uid"] else None,
            version=1,
        )
        session.add(hand)
        await session.commit()
        await session.refresh(hand)
        return hand


async def get_active_hand(table_id: str) -> PokerHand | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(PokerHand)
                .where(PokerHand.table_id == table_id, PokerHand.status == "active")
                .order_by(PokerHand.hand_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def get_latest_hand(table_id: str) -> PokerHand | None:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(PokerHand)
                .where(PokerHand.table_id == table_id)
                .order_by(PokerHand.hand_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def set_dm_message(table_id: str, user_id: int, message_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        seat = await session.get(PokerSeat, (table_id, user_id))
        if seat is not None:
            seat.dm_message_id = message_id
            await session.commit()


async def set_turn_notice(
    table_id: str,
    user_id: int,
    *,
    message_id: int | None,
    version: int = 0,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        seat = await session.get(PokerSeat, (table_id, user_id))
        if seat is not None:
            seat.turn_notice_message_id = message_id
            seat.turn_notice_version = version
            await session.commit()


async def save_hand(
    table_id: str,
    hand_id: int,
    state: dict,
    *,
    expected_version: int,
    timed_out_uid: int | None = None,
    garnish_pct: int = 0,
) -> PokerHand:
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        hand = await session.get(PokerHand, hand_id)
        if table is None or hand is None or hand.status != "active":
            raise PokerRepoError("hand_missing")
        if hand.version != expected_version:
            raise PokerRepoError("stale_action")
        seats = {
            seat.user_id: seat
            for seat in (
                (
                    await session.execute(
                        select(PokerSeat).where(
                            PokerSeat.table_id == table_id, PokerSeat.status == "active"
                        )
                    )
                )
                .scalars()
                .all()
            )
        }
        hand.state = state
        hand.street = state["street"]
        hand.version += 1
        hand.deadline = _now() + table.turn_timeout if state["current_uid"] else None
        table.last_event = state.get("last_action", "")[:256]
        if timed_out_uid is not None and timed_out_uid in seats:
            seats[timed_out_uid].consecutive_timeouts += 1

        # Keep the seat read model current during the hand so /me, the admin
        # economy snapshot and crash recovery all see the actual escrowed stack,
        # not merely the stack immediately after posting blinds.
        for uid, player_state in state["players"].items():
            seat = seats.get(int(uid))
            if seat is not None:
                seat.stack = int(player_state["stack"])
                seat.committed = int(player_state["total_bet"])

        if state["finished"]:
            hand.status = "finished"
            hand.finished_at = _now()
            hand.deadline = None
            table.status = "between"
            table.host_active_at = _now()
            result = state["result"] or {}
            for uid in state["players"]:
                seat = seats.get(int(uid))
                if seat is not None:
                    seat.ready = False
                    seat.committed = 0
                    if seat.consecutive_timeouts >= 2:
                        seat.sitting_out = True
            rake = int(result.get("rake", 0)) if table.mode == "money" else 0
            if rake:
                corp = await session.get(Corporation, 1)
                if corp is None:
                    session.add(Corporation(id=1))
                    await session.flush()
                await session.execute(
                    update(Corporation)
                    .where(Corporation.id == 1)
                    .values(
                        balance=Corporation.balance + rake,
                        total_tax=Corporation.total_tax + rake,
                        total_poker_rake=Corporation.total_poker_rake + rake,
                    )
                )
                assert table.chat_id is not None
                session.add(
                    _event(
                        table.chat_id,
                        table.host_id,
                        E.POKER_RAKE,
                        -rake,
                        0,
                        {"table_id": table_id, "hand_no": table.hand_no, "amount": rake},
                    )
                )
            # A result event is informational; stack movements remain escrowed.
            if table.mode == "money" and table.chat_id is not None:
                for uid, player_state in state["players"].items():
                    session.add(
                        _event(
                            table.chat_id,
                            int(uid),
                            E.POKER_RESULT,
                            0,
                            0,
                            {
                                "table_id": table_id,
                                "hand_no": table.hand_no,
                                "stack": int(player_state["stack"]),
                                "rake": rake,
                            },
                        )
                    )

            leaving = [seat for seat in seats.values() if seat.pending_leave or seat.pending_kick]
            for seat in leaving:
                await _cashout_in_session(
                    session,
                    table,
                    seat,
                    reason="kick" if seat.pending_kick else "leave",
                    garnish_pct=garnish_pct,
                )

            if table.close_after_hand:
                for seat in seats.values():
                    if seat.status == "active":
                        await _cashout_in_session(
                            session,
                            table,
                            seat,
                            reason="table_close",
                            garnish_pct=garnish_pct,
                        )
                table.status = "closed"
                table.close_after_hand = False

        await session.commit()
        await session.refresh(hand)
        return hand


async def reset_timeout_counter(table_id: str, user_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        seat = await session.get(PokerSeat, (table_id, user_id))
        if seat is not None:
            seat.consecutive_timeouts = 0
            await session.commit()


async def update_table(table_id: str, **values) -> PokerTable:
    allowed = {
        "access_mode",
        "max_seats",
        "turn_timeout",
        "paused",
        "last_event",
        "board_message_id",
        "host_active_at",
    }
    if not set(values) <= allowed:
        raise PokerRepoError("bad_table_update")
    factory = get_session_factory()
    async with factory() as session:
        table = await session.get(PokerTable, table_id)
        if table is None:
            raise PokerRepoError("table_missing")
        for key, value in values.items():
            setattr(table, key, value)
        await session.commit()
        await session.refresh(table)
        return table


async def count_active_seats(table_id: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count(PokerSeat.user_id)).where(
                        PokerSeat.table_id == table_id, PokerSeat.status == "active"
                    )
                )
            ).scalar_one()
        )


async def idle_host_tables(before: int) -> list[PokerTable]:
    factory = get_session_factory()
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(PokerTable).where(
                        PokerTable.status.in_({"lobby", "between"}),
                        PokerTable.host_active_at <= before,
                    )
                )
            )
            .scalars()
            .all()
        )
