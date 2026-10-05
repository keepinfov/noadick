"""A private chat must never be registered as a game chat."""

from __future__ import annotations

import os
import tempfile
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat as TgChat
from aiogram.types import Message
from aiogram.types import User as TgUser

import texts
from middlewares.registry import RegistryMiddleware

# Every middleware test gets its own chat/user pair: the DM-gate cooldown is a
# process-global bucket keyed by (chat_id, user_id), so reusing ids would leak
# state between tests.
_counter = 0


def _ids() -> tuple[int, int]:
    global _counter
    _counter += 1
    return -1007000 - _counter, 700000 + _counter


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    from db import engine as engine_mod
    from services import global_settings

    await engine_mod.dispose_engine()
    await engine_mod.init_db()
    global_settings.invalidate()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        global_settings.invalidate()
        os.unlink(path)


def _message(chat_id: int, chat_type: str, user_id: int, text: str = "/me") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=TgChat(id=chat_id, type=chat_type),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Вася"),
        text=text,
    )


def _bot() -> SimpleNamespace:
    return SimpleNamespace(me=AsyncMock(return_value=SimpleNamespace(username="noadick_bot")))


async def _call(event, bot=None):
    """Run the registry middleware with a recording handler."""
    handled: list = []

    async def handler(inner_event, _data):
        handled.append(inner_event)
        return "handled"

    data = {"bot": bot if bot is not None else _bot()}
    result = await RegistryMiddleware()(handler, event, data)
    return result, handled


# --- middleware --------------------------------------------------------------


async def test_private_message_registers_a_dm_marker_without_a_chat(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    from db.engine import get_session_factory
    from db.models import Chat, User

    _chat_id, user_id = _ids()
    result, handled = await _call(_message(user_id, "private", user_id))

    assert result == "handled"
    assert len(handled) == 1
    factory = get_session_factory()
    async with factory() as session:
        assert await session.get(Chat, user_id) is None
        user = await session.get(User, user_id)
    assert user is not None
    assert user.dm_started_at > 0


async def test_group_message_still_registers_the_chat(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    from db.engine import get_session_factory
    from db.models import Chat
    from repositories import chats as chats_repo

    chat_id, user_id = _ids()
    await chats_repo.upsert_user(user_id, "Вася", "vasya")
    await chats_repo.mark_dm_started(user_id)  # skip the DM gate, test the registry

    result, _handled = await _call(_message(chat_id, "supergroup", user_id, "/dick"))

    assert result == "handled"
    factory = get_session_factory()
    async with factory() as session:
        chat = await session.get(Chat, chat_id)
    assert chat is not None
    assert chat.type == "supergroup"


async def test_group_command_is_gated_until_the_dm_is_open(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    reply = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply)
    chat_id, user_id = _ids()

    result, handled = await _call(_message(chat_id, "supergroup", user_id, "/dick"))

    assert result is None
    assert handled == []
    reply.assert_awaited_once()
    assert reply.await_args.args[0] == texts.DM_GATE


async def test_group_command_passes_once_the_dm_is_open(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    reply = AsyncMock()
    monkeypatch.setattr(Message, "reply", reply)
    from repositories import chats as chats_repo

    chat_id, user_id = _ids()
    await chats_repo.upsert_user(user_id, "Вася", "vasya")
    await chats_repo.mark_dm_started(user_id)

    result, handled = await _call(_message(chat_id, "supergroup", user_id, "/dick"))

    assert result == "handled"
    assert len(handled) == 1
    reply.assert_not_awaited()


# --- target resolution -------------------------------------------------------


async def test_broadcast_modes_split_groups_from_dm_users(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    from repositories import chats as chats_repo
    from repositories import players as players_repo

    group, user_id = _ids()
    await chats_repo.upsert_chat(group, "Группа", "supergroup", "hash")
    await chats_repo.upsert_user(user_id, "Вася", "vasya")
    await chats_repo.mark_dm_started(user_id)

    assert await chats_repo.chat_ids_by_mode("groups") == [group]
    assert await chats_repo.chat_ids_by_mode("dm") == [user_id]
    assert await chats_repo.chat_ids_by_mode("all") == [group, user_id]
    # Nobody played yet, so nothing is "active".
    assert await chats_repo.chat_ids_by_mode("active") == []

    now = int(time.time())
    await players_repo.set_player_fields(group, user_id, size=1, last_play=now)
    await players_repo.set_player_fields(user_id, user_id, size=1, last_play=now)

    assert await chats_repo.chat_ids_by_mode("active") == [group, user_id]


async def test_chat_lists_and_counts_exclude_private_rows(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    from db.engine import get_session_factory
    from db.models import Chat
    from repositories import chats as chats_repo

    group, private_id = _ids()
    await chats_repo.upsert_chat(group, "Группа", "supergroup", "hash")
    factory = get_session_factory()
    async with factory() as session:
        session.add(Chat(chat_id=private_id, title="", type="private", hash=""))
        await session.commit()

    assert await chats_repo.count_chats() == 1
    stats = await chats_repo.global_stats()
    assert stats["chats"] == 1
    assert stats["dm_users"] == 0
    listed = await chats_repo.list_chats_with_owner()
    assert [chat.chat_id for chat, _owner in listed] == [group]
    assert await chats_repo.all_chat_ids() == [group]
    assert await chats_repo.chat_ids_by_mode("all") == [group]
    assert await chats_repo.active_chat_count() == 0


async def test_user_chat_sizes_ignore_private_rows(db, monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    from db.engine import get_session_factory
    from db.models import Chat, Player
    from repositories import players as players_repo

    group, user_id = _ids()
    await players_repo.set_player_fields(group, user_id, size=5)
    factory = get_session_factory()
    async with factory() as session:
        session.add(Chat(chat_id=user_id, title="", type="private", hash=""))
        await session.flush()
        session.add(Player(chat_id=user_id, user_id=user_id, name="Вася", size=3))
        await session.commit()

    assert await players_repo.user_chat_sizes(user_id) == [(group, "", 5)]
