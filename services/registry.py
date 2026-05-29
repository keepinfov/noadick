"""Chat/user registration helpers and one-time legacy data relink."""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from db.engine import get_session_factory
from db.models import LegacyChat, Player
from repositories.players import _apply_dict_to_player

# Chats whose legacy relink has already been attempted this process; avoids a
# DB lookup on every single update.
_checked: set[int] = set()


def chat_hash(chat_id: int) -> str:
    return hashlib.md5(str(chat_id).encode()).hexdigest()


async def relink_legacy(chat_id: int) -> int:
    """If migrated JSON data exists for this chat's md5 hash, import the players
    and bind them to the real chat_id. Returns number of players imported."""
    if chat_id in _checked:
        return 0
    _checked.add(chat_id)

    factory = get_session_factory()
    async with factory() as session:
        h = chat_hash(chat_id)
        legacy = (
            await session.execute(
                select(LegacyChat).where(
                    LegacyChat.hash == h,
                    LegacyChat.relinked_chat_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if legacy is None:
            return 0

        existing_ids = set(
            (
                await session.execute(
                    select(Player.user_id).where(Player.chat_id == chat_id)
                )
            ).scalars().all()
        )

        imported = 0
        for uid_str, data in (legacy.data or {}).items():
            uid = int(uid_str)
            if uid in existing_ids:
                continue
            p = Player(chat_id=chat_id, user_id=uid)
            _apply_dict_to_player(p, data)
            session.add(p)
            imported += 1

        legacy.relinked_chat_id = chat_id
        await session.commit()
        return imported
