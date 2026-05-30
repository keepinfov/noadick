from __future__ import annotations

import asyncio
import time

from sqlalchemy import delete, func, select, update

from db.engine import get_session_factory
from db.models import Chat, Player

# Per-chat locks serialize read-modify-write cycles so concurrent updates
# (game handlers + admin actions) in the same chat cannot lose writes.
_locks: dict[int, asyncio.Lock] = {}


def get_chat_lock(chat_id: int) -> asyncio.Lock:
    lock = _locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[chat_id] = lock
    return lock


# ---- conversion between ORM and the legacy dict shape used by game handlers ----

PlayerDict = dict
Storage = dict[str, PlayerDict]


def player_to_dict(p: Player) -> PlayerDict:
    d: PlayerDict = {
        "name": p.name,
        "size": p.size,
        "last": p.last_play,
        "chat_banned": bool(p.is_chat_banned),
    }
    if p.disease_id:
        d["disease"] = {"id": p.disease_id, "caught_at": p.disease_caught_at}
    return d


def _apply_dict_to_player(p: Player, data: PlayerDict) -> None:
    p.name = data.get("name", p.name)
    p.size = int(data.get("size", p.size))
    p.last_play = int(data.get("last", p.last_play))
    if "chat_banned" in data:
        p.is_chat_banned = bool(data["chat_banned"])
    disease = data.get("disease")
    if disease:
        p.disease_id = disease.get("id")
        p.disease_caught_at = disease.get("caught_at")
    else:
        p.disease_id = None
        p.disease_caught_at = None


async def ensure_chat(session, chat_id: int) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        chat = Chat(chat_id=chat_id)
        session.add(chat)
    return chat


# ---- legacy-compatible whole-chat API (used by game handlers) ----


async def get_storage(chat_id: int) -> Storage:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(select(Player).where(Player.chat_id == chat_id))
        ).scalars().all()
        return {str(p.user_id): player_to_dict(p) for p in rows}


async def save_storage(chat_id: int, storage: Storage) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await ensure_chat(session, chat_id)
        existing = {
            p.user_id: p
            for p in (
                await session.execute(select(Player).where(Player.chat_id == chat_id))
            ).scalars().all()
        }
        for uid_str, data in storage.items():
            uid = int(uid_str)
            p = existing.get(uid)
            if p is None:
                p = Player(chat_id=chat_id, user_id=uid)
                session.add(p)
            _apply_dict_to_player(p, data)
        await session.commit()


# ---- granular API (used by admin actions and stats) ----


async def get_player(chat_id: int, user_id: int) -> Player | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(Player, (chat_id, user_id))


async def list_players(chat_id: int) -> list[Player]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Player)
                .where(Player.chat_id == chat_id)
                .order_by(Player.size.desc())
            )
        ).scalars().all()
        return list(rows)


async def top_players(chat_id: int, limit: int = 10) -> list[Player]:
    return (await list_players(chat_id))[:limit]


async def list_players_page(
    chat_id: int, offset: int, limit: int
) -> list[Player]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Player)
                .where(Player.chat_id == chat_id)
                .order_by(Player.size.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)


async def count_players(chat_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(func.count(Player.user_id)).where(Player.chat_id == chat_id)
            )
        ).scalar_one()


async def get_rank(chat_id: int, user_id: int) -> int:
    players = await list_players(chat_id)
    for i, p in enumerate(players):
        if p.user_id == user_id:
            return i + 1
    return len(players) + 1


async def set_player_fields(chat_id: int, user_id: int, **fields) -> Player | None:
    factory = get_session_factory()
    async with factory() as session:
        await ensure_chat(session, chat_id)
        p = await session.get(Player, (chat_id, user_id))
        if p is None:
            p = Player(chat_id=chat_id, user_id=user_id, name=str(user_id))
            session.add(p)
        for key, value in fields.items():
            setattr(p, key, value)
        await session.commit()
        await session.refresh(p)
        return p


async def delete_player(chat_id: int, user_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        p = await session.get(Player, (chat_id, user_id))
        if p is None:
            return False
        await session.delete(p)
        await session.commit()
        return True


async def reset_chat_players(chat_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            delete(Player).where(Player.chat_id == chat_id)
        )
        await session.commit()
        return result.rowcount or 0


async def zero_chat_sizes(chat_id: int) -> int:
    """Reset every player's size to 0 in a chat (keeps rows, e.g. local bans).
    Returns the number of players affected."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            update(Player).where(Player.chat_id == chat_id).values(size=0)
        )
        await session.commit()
        return result.rowcount or 0


async def find_players(query: str, limit: int = 25) -> list[Player]:
    """Search players across all chats by user_id (numeric) or name (substring)."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Player)
        if query.isdigit():
            stmt = stmt.where(Player.user_id == int(query))
        else:
            stmt = stmt.where(Player.name.ilike(f"%{query}%"))
        rows = (
            await session.execute(stmt.order_by(Player.size.desc()).limit(limit))
        ).scalars().all()
        return list(rows)


async def chat_player_stats(chat_id: int) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        count, total, biggest = (
            await session.execute(
                select(
                    func.count(Player.user_id),
                    func.coalesce(func.sum(Player.size), 0),
                    func.coalesce(func.max(Player.size), 0),
                ).where(Player.chat_id == chat_id)
            )
        ).one()
        return {"players": count, "total_size": total, "biggest": biggest}


async def user_chat_sizes(user_id: int) -> list[tuple[int, str, int]]:
    """All (chat_id, title, size) entries for a user across non-banned chats,
    biggest first."""
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Player.chat_id, Chat.title, Player.size)
                .join(Chat, Chat.chat_id == Player.chat_id)
                .where(Player.user_id == user_id, Chat.is_banned.is_(False))
                .order_by(Player.size.desc())
            )
        ).all()
        return [(cid, title, size) for cid, title, size in rows]


async def global_rank_for(user_id: int, chat_id: int, size: int) -> int:
    """Rank within a chat without loading every player: 1 + how many players
    in that chat are strictly bigger."""
    factory = get_session_factory()
    async with factory() as session:
        bigger = (
            await session.execute(
                select(func.count(Player.user_id))
                .where(Player.chat_id == chat_id, Player.size > size)
            )
        ).scalar_one()
        return bigger + 1


async def cure_expired(chat_id: int, user_id: int) -> None:
    """Persist disease expiry computed elsewhere (clears disease columns)."""
    await set_player_fields(
        chat_id, user_id, disease_id=None, disease_caught_at=None
    )


def now_ts() -> int:
    return int(time.time())
