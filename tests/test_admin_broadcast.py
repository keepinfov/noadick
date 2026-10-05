"""Broadcast composer: recipients, preview and delivery."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import texts
from handlers import admin
from services import admin_actions

GROUP = -1007001
PLAYER = 777


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


def _fsm() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=100, user_id=7),
    )


def _callback(data: str = "adm:bcpre") -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=7),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )


async def _seed() -> None:
    from repositories import chats as chats_repo

    await chats_repo.upsert_chat(GROUP, "Группа", "supergroup", "hash")
    await chats_repo.upsert_user(PLAYER, "Вася", "vasya")
    await chats_repo.mark_dm_started(PLAYER)


# --- target resolution -------------------------------------------------------


async def test_presets_and_explicit_picks_are_deduped(db) -> None:
    await _seed()

    targets = await admin_actions.resolve_broadcast_targets(
        [
            {"kind": "groups"},
            {"kind": "user", "id": PLAYER, "label": "Вася"},
            {"kind": "chat", "id": GROUP, "label": "Группа"},
            {"kind": "user", "id": PLAYER, "label": "Вася"},
        ],
        active_days=30,
    )

    assert [(t.chat_id, t.is_dm) for t in targets] == [(GROUP, False), (PLAYER, True)]


async def test_dm_and_all_modes_follow_opened_dms(db) -> None:
    await _seed()

    dm_only = await admin_actions.resolve_broadcast_targets([{"kind": "dm"}], active_days=30)
    everything = await admin_actions.resolve_broadcast_targets([{"kind": "all"}], active_days=30)

    assert [(t.chat_id, t.is_dm) for t in dm_only] == [(PLAYER, True)]
    assert [(t.chat_id, t.is_dm) for t in everything] == [(GROUP, False), (PLAYER, True)]
    assert await admin_actions.count_recipient({"kind": "all"}, active_days=30) == 2


async def test_lookup_by_username_id_and_chat(db) -> None:
    await _seed()

    assert await admin_actions.find_user_target("@vasya") == (PLAYER, "Вася")
    assert await admin_actions.find_user_target(str(PLAYER)) == (PLAYER, "Вася")
    assert await admin_actions.find_user_target("@nobody") is None
    assert await admin_actions.find_chat_target(str(GROUP)) == (GROUP, "Группа")
    assert await admin_actions.find_chat_target("не число") is None


# --- composer screens --------------------------------------------------------


async def test_text_message_becomes_the_letter_and_renders_the_composer(db) -> None:
    await _seed()
    state = _fsm()
    message = SimpleNamespace(text="<b>Привет</b>", html_text="<b>Привет</b>", answer=AsyncMock())

    await admin.msg_bcast(message, state)

    data = await state.get_data()
    assert data["bcast_text"] == "<b>Привет</b>"
    message.answer.assert_awaited_once()
    rendered = message.answer.await_args.args[0]
    assert "<b>Привет</b>" in rendered
    assert "Получатели: пока никого" in rendered


async def test_recipients_screen_lists_added_presets_and_removals(db) -> None:
    await _seed()
    state = _fsm()
    await state.update_data(bcast_recipients=[{"kind": "all"}, {"kind": "user", "id": PLAYER}])

    rendered, keyboard = await admin.render_bcast_recipients(state)

    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert texts.BCAST_PRESET_LABELS["groups"] in labels
    assert any(label.startswith("❌ 🌐") for label in labels)
    assert any(label.startswith("❌ 👤") for label in labels)
    assert "Итого доставок: 2" in rendered
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


async def test_preview_requires_text_and_recipients(db) -> None:
    await _seed()
    state = _fsm()
    callback = _callback()

    await admin.cb_bcast_preview(callback, state)
    callback.answer.assert_awaited_once_with(texts.admin_bcast_no_text(), show_alert=True)

    await state.update_data(bcast_text="<b>Привет</b>")
    callback = _callback()
    await admin.cb_bcast_preview(callback, state)
    callback.answer.assert_awaited_once_with(texts.admin_bcast_no_targets(), show_alert=True)

    await state.update_data(bcast_recipients=[{"kind": "all"}])
    callback = _callback()
    await admin.cb_bcast_preview(callback, state)
    rendered = callback.message.edit_text.await_args.args[0]
    assert texts.admin_bcast_target_text("<b>Привет</b>", [], 0).split("\n")[0] in rendered
    assert "Итого доставок: 2" in rendered


# --- delivery ----------------------------------------------------------------


async def test_send_delivers_to_groups_and_dms_and_logs_once(db) -> None:
    from repositories import broadcasts as broadcasts_repo

    await _seed()
    state = _fsm()
    await state.update_data(
        bcast_text="<b>Привет</b>",
        bcast_recipients=[
            {"kind": "chat", "id": GROUP, "label": "Группа"},
            {"kind": "user", "id": PLAYER, "label": "Вася"},
        ],
    )
    bot = AsyncMock()
    callback = _callback("adm:bcgo")

    await admin.cb_do_bcast(callback, state, bot)

    assert {call.args[0] for call in bot.send_message.await_args_list} == {GROUP, PLAYER}
    assert bot.send_message.await_count == 2
    assert await state.get_data() == {}

    rows = await broadcasts_repo.list_broadcasts()
    assert len(rows) == 1
    assert (rows[0].sent, rows[0].failed, rows[0].target_mode) == (2, 0, "chat+user")
    assert callback.message.answer.await_count == 1
