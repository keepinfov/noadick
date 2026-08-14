from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from db.engine import get_session_factory
from db.models import Deposit, Loan, Player, PokerSeat, PokerTable


@dataclass(frozen=True)
class Wealth:
    chat_id: int
    user_id: int
    name: str
    liquid: int = 0
    deposits: int = 0
    poker: int = 0
    debt: int = 0

    @property
    def net(self) -> int:
        return self.liquid + self.deposits + self.poker - self.debt


async def rows(
    *,
    chat_id: int | None = None,
    chat_ids: list[int] | None = None,
    user_id: int | None = None,
) -> list[Wealth]:
    factory = get_session_factory()
    async with factory() as session:
        player_stmt = select(Player)
        deposit_stmt = select(Deposit)
        loan_stmt = select(Loan)
        poker_stmt = (
            select(PokerTable.chat_id, PokerSeat)
            .join(PokerTable, PokerTable.table_id == PokerSeat.table_id)
            .where(PokerSeat.status == "active", PokerTable.mode == "money")
        )
        if chat_id is not None:
            player_stmt = player_stmt.where(Player.chat_id == chat_id)
            deposit_stmt = deposit_stmt.where(Deposit.chat_id == chat_id)
            loan_stmt = loan_stmt.where(Loan.chat_id == chat_id)
            poker_stmt = poker_stmt.where(PokerTable.chat_id == chat_id)
        elif chat_ids is not None:
            player_stmt = player_stmt.where(Player.chat_id.in_(chat_ids))
            deposit_stmt = deposit_stmt.where(Deposit.chat_id.in_(chat_ids))
            loan_stmt = loan_stmt.where(Loan.chat_id.in_(chat_ids))
            poker_stmt = poker_stmt.where(PokerTable.chat_id.in_(chat_ids))
        if user_id is not None:
            player_stmt = player_stmt.where(Player.user_id == user_id)
            deposit_stmt = deposit_stmt.where(Deposit.user_id == user_id)
            loan_stmt = loan_stmt.where(Loan.user_id == user_id)
            poker_stmt = poker_stmt.where(PokerSeat.user_id == user_id)

        players = list((await session.execute(player_stmt)).scalars())
        deposits = list((await session.execute(deposit_stmt)).scalars())
        loans = list((await session.execute(loan_stmt)).scalars())
        poker = (await session.execute(poker_stmt)).all()

    values: dict[tuple[int, int], dict[str, int | str]] = {}

    def item(key: tuple[int, int]) -> dict[str, int | str]:
        return values.setdefault(
            key,
            {"name": str(key[1]), "liquid": 0, "deposits": 0, "poker": 0, "debt": 0},
        )

    for player in players:
        entry = item((int(player.chat_id), int(player.user_id)))
        entry["name"] = player.name or str(player.user_id)
        entry["liquid"] = int(player.size)
    for deposit in deposits:
        entry = item((int(deposit.chat_id), int(deposit.user_id)))
        entry["deposits"] = int(deposit.principal + deposit.accrued)
    for loan in loans:
        entry = item((int(loan.chat_id), int(loan.user_id)))
        entry["debt"] = int(loan.principal + loan.accrued_interest)
    for poker_chat_id, seat in poker:
        if poker_chat_id is None:
            continue
        entry = item((int(poker_chat_id), int(seat.user_id)))
        if entry["name"] == str(seat.user_id):
            entry["name"] = seat.name or str(seat.user_id)
        entry["poker"] = int(entry["poker"]) + int(seat.stack + seat.committed)

    result = [
        Wealth(
            chat_id=key[0],
            user_id=key[1],
            name=str(value["name"]),
            liquid=int(value["liquid"]),
            deposits=int(value["deposits"]),
            poker=int(value["poker"]),
            debt=int(value["debt"]),
        )
        for key, value in values.items()
    ]
    return sorted(result, key=lambda value: (-value.net, value.user_id))


async def chat_rows(chat_id: int) -> list[Wealth]:
    return await rows(chat_id=chat_id)
