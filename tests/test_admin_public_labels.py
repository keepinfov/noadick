from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import texts
from handlers import admin
from services.admin_actions import ActionResult


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    from db import engine as engine_mod

    await engine_mod.dispose_engine()
    await engine_mod.init_db()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        os.unlink(path)


async def _seed_user(user_id: int = 42, *, label: str | None = None) -> None:
    from db.engine import get_session_factory
    from db.models import User
    from repositories import chats

    await chats.upsert_user(user_id, "Игрок", "player")
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.public_label = label
        user.notes = "причина блокировки"
        await session.commit()


async def test_admin_action_sets_replaces_and_clears_label_with_atomic_audit(db) -> None:
    from db.engine import get_session_factory
    from db.models import AuditLog, User
    from services import admin_actions

    await _seed_user(label=None)

    set_result = await admin_actions.set_public_label(7, 42, "  <легенда> & тест  ")
    replace_result = await admin_actions.set_public_label(8, 42, "Ветеран")
    clear_result = await admin_actions.set_public_label(9, 42, " - ")

    assert set_result.ok and replace_result.ok and clear_result.ok
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, 42)
        audits = list(
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.action == "set_public_label")
                    .order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    assert user is not None
    assert user.public_label is None
    assert user.notes == "причина блокировки"
    assert [(row.actor_id, row.target_chat, row.target_user) for row in audits] == [
        (7, None, 42),
        (8, None, 42),
        (9, None, 42),
    ]
    assert [row.payload for row in audits] == [
        {"before": None, "after": "<легенда> & тест"},
        {"before": "<легенда> & тест", "after": "Ветеран"},
        {"before": "Ветеран", "after": None},
    ]


@pytest.mark.parametrize("label", ["", "   ", "строка\nвторая", "строка\rвторая", "я" * 81])
async def test_admin_action_rejects_invalid_label_without_mutation_or_audit(db, label) -> None:
    from db.engine import get_session_factory
    from db.models import AuditLog, User
    from services import admin_actions

    await _seed_user(label="Сохранить")

    result = await admin_actions.set_public_label(7, 42, label)

    assert not result.ok
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, 42)
        audit_count = len(list((await session.execute(select(AuditLog))).scalars()))
    assert user is not None
    assert (user.public_label, user.notes) == ("Сохранить", "причина блокировки")
    assert audit_count == 0


async def test_admin_action_does_not_synthesize_missing_user_or_audit(db) -> None:
    from db.engine import get_session_factory
    from db.models import AuditLog, User
    from services import admin_actions

    result = await admin_actions.set_public_label(7, 404, "Лейбл")

    assert not result.ok
    factory = get_session_factory()
    async with factory() as session:
        assert await session.get(User, 404) is None
        assert list((await session.execute(select(AuditLog))).scalars()) == []


async def test_admin_action_rejects_non_string_without_mutation_or_audit(db) -> None:
    from db.engine import get_session_factory
    from db.models import AuditLog, User
    from services import admin_actions

    await _seed_user(label="Сохранить")

    result = await admin_actions.set_public_label(7, 42, cast(Any, 123))

    assert not result.ok
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, 42)
        audits = list((await session.execute(select(AuditLog))).scalars())
    assert user is not None
    assert (user.public_label, user.notes) == ("Сохранить", "причина блокировки")
    assert audits == []


async def test_ban_and_unban_clear_notes_but_preserve_public_label(db) -> None:
    from repositories import chats

    await _seed_user(label="Ветеран")

    await chats.set_user_banned(42, True, reason="новая причина")
    banned = await chats.get_user(42)
    assert banned is not None
    assert (banned.notes, banned.public_label) == ("новая причина", "Ветеран")

    await chats.set_user_banned(42, False)
    unbanned = await chats.get_user(42)
    assert unbanned is not None
    assert unbanned.notes is None
    assert unbanned.public_label == "Ветеран"


async def test_admin_action_rolls_back_label_when_atomic_commit_fails(db, monkeypatch) -> None:
    from db.engine import get_session_factory
    from db.models import AuditLog, User
    from services import admin_actions

    await _seed_user(label="Старый")

    async def fail_commit(_session) -> None:
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await admin_actions.set_public_label(7, 42, "Новый")

    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, 42)
        audits = list((await session.execute(select(AuditLog))).scalars())
    assert user is not None
    assert user.public_label == "Старый"
    assert audits == []


async def test_render_player_escapes_label_and_builds_safe_callback(db) -> None:
    from repositories import players

    chat_id = -1007001
    user_id = 42
    await _seed_user(user_id, label="<легенда> & тест")
    await players.set_player_fields(chat_id, user_id, name="Игрок", size=12)

    rendered, markup = await admin.render_player(chat_id, user_id)

    assert "🏷 Публичный лейбл: &lt;легенда&gt; &amp; тест" in rendered
    button = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if button.text == texts.BTN_SET_PUBLIC_LABEL
    )
    assert button.callback_data == f"adm:setlabel:{chat_id}:{user_id}"
    worst_case = "adm:setlabel:-9223372036854775808:9223372036854775807"
    worst_cancel = "adm:p:-9223372036854775808:9223372036854775807"
    assert len(worst_case.encode()) == 53
    assert len(worst_case.encode()) <= 64
    assert len(worst_cancel.encode()) == 46
    assert len(worst_cancel.encode()) <= 64


async def test_label_callback_enters_fsm_with_dynamic_player_cancel() -> None:
    callback = SimpleNamespace(
        data="adm:setlabel:-1007001:42",
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    state = AsyncMock()

    await admin.cb_set_public_label(callback, state)

    state.set_state.assert_awaited_once_with(admin.AdminStates.set_public_label)
    state.update_data.assert_awaited_once_with(chat_id=-1007001, user_id=42)
    edit_call = callback.message.edit_text.await_args
    assert edit_call.args[0] == texts.ADMIN_ENTER_PUBLIC_LABEL
    cancel = edit_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert cancel.callback_data == "adm:p:-1007001:42"
    callback.answer.assert_awaited_once()


async def test_valid_label_input_is_trimmed_clears_fsm_and_returns_card(monkeypatch) -> None:
    message = SimpleNamespace(
        text="  Ветеран  ",
        from_user=SimpleNamespace(id=7),
        answer=AsyncMock(),
    )
    state = AsyncMock()
    state.get_data.return_value = {"chat_id": -1007001, "user_id": 42}
    action = AsyncMock(return_value=ActionResult(True, "ok"))
    render = AsyncMock(return_value=("карточка", SimpleNamespace()))
    monkeypatch.setattr(admin.admin_actions, "set_public_label", action)
    monkeypatch.setattr(admin, "render_player", render)

    await admin.msg_set_public_label(message, state)

    action.assert_awaited_once_with(7, 42, "Ветеран")
    state.clear.assert_awaited_once()
    render.assert_awaited_once_with(-1007001, 42)
    assert message.answer.await_args.args[0] == "карточка"
    assert message.answer.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.parametrize("raw", [None, "", "   ", "первая\nвторая", "первая\rвторая", "я" * 81])
async def test_invalid_label_input_preserves_fsm_and_repeats_prompt(monkeypatch, raw) -> None:
    message = SimpleNamespace(
        text=raw,
        from_user=SimpleNamespace(id=7),
        answer=AsyncMock(),
    )
    state = AsyncMock()
    state.get_data.return_value = {"chat_id": -1007001, "user_id": 42}
    action = AsyncMock()
    monkeypatch.setattr(admin.admin_actions, "set_public_label", action)

    await admin.msg_set_public_label(message, state)

    action.assert_not_awaited()
    state.clear.assert_not_awaited()
    response = message.answer.await_args
    assert texts.ADMIN_PUBLIC_LABEL_INVALID in response.args[0]
    assert texts.ADMIN_ENTER_PUBLIC_LABEL in response.args[0]
    cancel = response.kwargs["reply_markup"].inline_keyboard[0][0]
    assert cancel.callback_data == "adm:p:-1007001:42"


async def test_dash_input_reaches_service_as_clear_and_failed_action_keeps_fsm(monkeypatch) -> None:
    message = SimpleNamespace(
        text=" - ",
        from_user=SimpleNamespace(id=7),
        answer=AsyncMock(),
    )
    state = AsyncMock()
    state.get_data.return_value = {"chat_id": -1007001, "user_id": 404}
    action = AsyncMock(return_value=ActionResult(False, texts.RES_USER_NOT_FOUND))
    monkeypatch.setattr(admin.admin_actions, "set_public_label", action)

    await admin.msg_set_public_label(message, state)

    action.assert_awaited_once_with(7, 404, None)
    state.clear.assert_not_awaited()
    response = message.answer.await_args
    assert texts.RES_USER_NOT_FOUND in response.args[0]
    assert texts.ADMIN_ENTER_PUBLIC_LABEL in response.args[0]
