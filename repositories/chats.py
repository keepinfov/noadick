from __future__ import annotations

from sqlalchemy import func, select

from db.engine import get_session_factory
from db.models import Chat, Player, User


async def upsert_chat(chat_id: int, title: str, ctype: str, chat_hash: str) -> Chat:
    factory = get_session_factory()
    async with factory() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            chat = Chat(chat_id=chat_id, title=title, type=ctype, hash=chat_hash)
            session.add(chat)
        else:
            chat.title = title
            chat.type = ctype
            chat.hash = chat_hash
        await session.commit()
        await session.refresh(chat)
        return chat


async def upsert_user(
    user_id: int, first_name: str, username: str | None
) -> User:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(user_id=user_id, first_name=first_name, username=username)
            session.add(user)
        else:
            user.first_name = first_name
            user.username = username
        await session.commit()
        await session.refresh(user)
        return user


async def get_chat(chat_id: int) -> Chat | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(Chat, chat_id)


async def list_chats(offset: int = 0, limit: int = 10) -> list[Chat]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(Chat).order_by(Chat.title).offset(offset).limit(limit)
            )
        ).scalars().all()
        return list(rows)


async def count_chats() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(select(func.count(Chat.chat_id)))
        ).scalar_one()


async def all_chat_ids(include_banned: bool = False) -> list[int]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Chat.chat_id)
        if not include_banned:
            stmt = stmt.where(Chat.is_banned.is_(False))
        return list((await session.execute(stmt)).scalars().all())


async def set_chat_banned(chat_id: int, banned: bool) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            return False
        chat.is_banned = banned
        await session.commit()
        return True


async def get_user(user_id: int) -> User | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(User, user_id)


async def set_user_banned(user_id: int, banned: bool) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(user_id=user_id, first_name=str(user_id), is_banned=banned)
            session.add(user)
        else:
            user.is_banned = banned
        await session.commit()
        return True


async def global_stats() -> dict:
    factory = get_session_factory()
    async with factory() as session:
        chats = (await session.execute(select(func.count(Chat.chat_id)))).scalar_one()
        users = (await session.execute(select(func.count(User.user_id)))).scalar_one()
        players, total = (
            await session.execute(
                select(
                    func.count(Player.user_id),
                    func.coalesce(func.sum(Player.size), 0),
                )
            )
        ).one()
        return {
            "chats": chats,
            "users": users,
            "players": players,
            "total_size": total,
        }
