from __future__ import annotations

import time

from sqlalchemy import func, select

from db.engine import get_session_factory
from db.models import Chat, Player, User

# A private chat is not a registered chat: it is only a delivery channel for one
# user, tracked by ``User.dm_started_at``. Everything that lists or counts chats
# excludes ``type = 'private'`` (and keeps legacy rows with an unset type).
PRIVATE_CHAT = "private"


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


async def upsert_user(user_id: int, first_name: str, username: str | None) -> User:
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
            (
                await session.execute(
                    select(Chat)
                    .where(Chat.type != PRIVATE_CHAT)
                    .order_by(Chat.title)
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def count_chats(name_filter: str | None = None) -> int:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(func.count(Chat.chat_id)).where(Chat.type != PRIVATE_CHAT)
        if name_filter:
            stmt = stmt.where(Chat.title.ilike(f"%{name_filter}%"))
        return (await session.execute(stmt)).scalar_one()


async def all_chat_ids(include_banned: bool = False) -> list[int]:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Chat.chat_id).where(Chat.type != PRIVATE_CHAT)
        if not include_banned:
            stmt = stmt.where(Chat.is_banned.is_(False))
        return list((await session.execute(stmt)).scalars().all())


ACTIVE_DAYS_DEFAULT = 30


def _active_cutoff(active_days: int) -> int:
    return int(time.time()) - active_days * 86400


async def chat_ids_by_mode(mode: str, active_days: int = ACTIVE_DAYS_DEFAULT) -> list[int]:
    """Delivery ids for one broadcast mode (groups first, then DM users)."""
    return [chat_id for chat_id, _is_dm in await broadcast_targets(mode, active_days)]


async def broadcast_targets(
    mode: str, active_days: int = ACTIVE_DAYS_DEFAULT
) -> list[tuple[int, bool]]:
    """``(chat_id, is_dm)`` deliveries for one broadcast mode.

    Groups come from the registered chats; direct messages come from the users
    who opened a DM, because a private chat is never a chat. Modes:
    - "groups": non-banned group chats;
    - "dm": users with an open DM;
    - "active": active group chats plus active DM users;
    - "all" (default): every non-banned group plus every DM user.
    """
    factory = get_session_factory()
    groups: list[int] = []
    if mode != "dm":
        async with factory() as session:
            stmt = select(Chat.chat_id).where(Chat.is_banned.is_(False), Chat.type != PRIVATE_CHAT)
            if mode == "active":
                recent = (
                    select(Player.chat_id)
                    .where(Player.last_play >= _active_cutoff(active_days))
                    .distinct()
                )
                stmt = stmt.where(Chat.chat_id.in_(recent))
            groups = [int(cid) for cid in (await session.execute(stmt)).scalars().all()]

    targets: list[tuple[int, bool]] = [(chat_id, False) for chat_id in groups]
    if mode not in {"dm", "all", "active"}:
        return targets

    users = await dm_user_ids(active_days if mode == "active" else None)
    seen = set(groups)
    targets.extend((user_id, True) for user_id in users if user_id not in seen)
    return targets


async def mark_dm_started(user_id: int, now: int | None = None) -> bool:
    """Record that the user has opened a private chat with the bot."""
    timestamp = int(time.time()) if now is None else now
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return False
        if not user.dm_started_at:
            user.dm_started_at = timestamp
            await session.commit()
        return True


async def dm_user_ids(active_days: int | None = None) -> list[int]:
    """Users who opened a DM, optionally only those who played recently."""
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(User.user_id).where(User.dm_started_at > 0, User.is_banned.is_(False))
        if active_days is not None:
            recent = (
                select(Player.user_id)
                .where(Player.last_play >= _active_cutoff(active_days))
                .distinct()
            )
            stmt = stmt.where(User.user_id.in_(recent))
        return [int(uid) for uid in (await session.execute(stmt)).scalars().all()]


async def count_dm_users() -> int:
    """How many users have an open DM (used by global statistics)."""
    factory = get_session_factory()
    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count(User.user_id)).where(User.dm_started_at > 0)
                )
            ).scalar_one()
        )


async def list_chats_with_owner(
    offset: int = 0,
    limit: int = 10,
    sort: str = "n",
    name_filter: str | None = None,
) -> list[tuple[Chat, User | None]]:
    """Registered game chats with their (always empty) owner slot.

    The owner used to label private chats; a DM is no longer a chat, so the
    second element is kept for call-site compatibility and is always ``None``.

    ``sort`` is a whitelisted code (never interpolated): n=title, a=last
    activity, c=newest, s=player count. ``name_filter`` matches the title."""
    factory = get_session_factory()
    async with factory() as session:
        pc = (
            select(Player.chat_id.label("cid"), func.count().label("pc"))
            .group_by(Player.chat_id)
            .subquery()
        )
        stmt = select(Chat).outerjoin(pc, pc.c.cid == Chat.chat_id).where(Chat.type != PRIVATE_CHAT)
        if name_filter:
            stmt = stmt.where(Chat.title.ilike(f"%{name_filter}%"))
        if sort == "a":
            stmt = stmt.order_by(Chat.updated_at.desc())
        elif sort == "c":
            stmt = stmt.order_by(Chat.created_at.desc())
        elif sort == "s":
            stmt = stmt.order_by(func.coalesce(pc.c.pc, 0).desc())
        else:
            stmt = stmt.order_by(Chat.title)
        rows = (await session.execute(stmt.offset(offset).limit(limit))).scalars().all()
        return [(chat, None) for chat in rows]


async def active_chat_count(active_days: int = ACTIVE_DAYS_DEFAULT) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(func.count(func.distinct(Chat.chat_id)))
                .select_from(Chat)
                .join(Player, Player.chat_id == Chat.chat_id)
                .where(
                    Chat.is_banned.is_(False),
                    Chat.type != PRIVATE_CHAT,
                    Player.last_play >= _active_cutoff(active_days),
                )
            )
        ).scalar_one()


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


async def get_user_by_username(username: str) -> User | None:
    """Look a user up by Telegram handle (stored without the leading @)."""
    handle = username.lstrip("@").strip()
    if not handle:
        return None
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                select(User).where(func.lower(User.username) == handle.lower()).limit(1)
            )
        ).scalar_one_or_none()


async def set_user_banned(
    user_id: int,
    banned: bool,
    reason: str | None = None,
    ban_until: int | None = None,
) -> bool:
    now = int(time.time())
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            user = User(
                user_id=user_id,
                first_name=str(user_id),
                is_banned=banned,
                notes=reason if banned else None,
                banned_at=now if banned else None,
                ban_until=ban_until if banned else None,
            )
            session.add(user)
        else:
            user.is_banned = banned
            user.notes = reason if banned else None
            user.banned_at = now if banned else None
            user.ban_until = ban_until if banned else None
        await session.commit()
        return True


async def global_stats() -> dict:
    factory = get_session_factory()
    async with factory() as session:
        chats = (
            await session.execute(select(func.count(Chat.chat_id)).where(Chat.type != PRIVATE_CHAT))
        ).scalar_one()
        users = (await session.execute(select(func.count(User.user_id)))).scalar_one()
        dm_users = (
            await session.execute(select(func.count(User.user_id)).where(User.dm_started_at > 0))
        ).scalar_one()
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
            "dm_users": dm_users,
            "players": players,
            "total_size": total,
        }
