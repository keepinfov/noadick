from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import texts
from handlers import admin
from services import casino


def _callback(data: str):
    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def _draft(**overrides) -> dict:
    value = {
        "original_slot_value": 1,
        "symbols": [casino.BAR, casino.BAR, casino.BAR],
        "payout_kind": "multiplier",
        "payout_value": 5,
        "page": 0,
    }
    value.update(overrides)
    return value


def test_main_admin_menu_links_to_compact_casino_panel() -> None:
    keyboard = admin.main_menu_kb()
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    casino_button = next(button for button in buttons if button.text == texts.BTN_CASINO_PAYOUTS)

    assert casino_button.callback_data == "adm:cpay:0"
    assert len(keyboard.inline_keyboard) == 10


async def test_casino_rule_list_is_paged_and_shows_exact_payouts(monkeypatch) -> None:
    rules = [
        SimpleNamespace(slot_value=64, payout_kind="fixed", payout_value=250),
    ]
    count = AsyncMock(return_value=9)
    list_rules = AsyncMock(return_value=rules)
    monkeypatch.setattr(admin.casino, "count_payout_rules", count)
    monkeypatch.setattr(admin.casino, "list_payout_rules", list_rules)

    rendered, keyboard = await admin.render_casino_payouts(1)

    assert "комбинаций: <b>9</b>" in rendered
    assert "страница 2/2" in rendered
    list_rules.assert_awaited_once_with(offset=8, limit=8)
    first = keyboard.inline_keyboard[0][0]
    assert first.text == "7️⃣ · 7️⃣ · 7️⃣ — 250 см"
    assert first.callback_data == "adm:cpedit:64:1"
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "adm:cpnew:1" in callbacks
    assert "adm:home" in callbacks


async def test_new_rule_selects_first_free_combination_and_opens_three_reels(monkeypatch) -> None:
    monkeypatch.setattr(
        admin.casino,
        "list_payout_rules",
        AsyncMock(
            return_value=[
                SimpleNamespace(slot_value=1),
                SimpleNamespace(slot_value=2),
            ]
        ),
    )
    callback = _callback("adm:cpnew:2")
    state = AsyncMock()

    await admin.cb_casino_payout_new(callback, state)

    state.clear.assert_awaited_once()
    state.set_state.assert_awaited_once_with(admin.AdminStates.casino_payout_edit)
    stored = state.update_data.await_args.kwargs[admin.CASINO_DRAFT_KEY]
    assert stored == {
        "original_slot_value": None,
        "symbols": list(casino.decode_slot(3)),
        "payout_kind": "multiplier",
        "payout_value": 1,
        "page": 2,
    }
    keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
    reels = keyboard.inline_keyboard[0]
    assert [button.callback_data for button in reels] == [
        "adm:cpreel:0",
        "adm:cpreel:1",
        "adm:cpreel:2",
    ]
    assert all(len((button.callback_data or "").encode()) <= 64 for button in reels)


async def test_existing_rule_opens_with_persisted_values(monkeypatch) -> None:
    rule = SimpleNamespace(slot_value=64, payout_kind="fixed", payout_value=250)
    monkeypatch.setattr(admin.casino, "get_payout_rule", AsyncMock(return_value=rule))
    callback = _callback("adm:cpedit:64:2")
    state = AsyncMock()

    await admin.cb_casino_payout_edit(callback, state)

    stored = state.update_data.await_args.kwargs[admin.CASINO_DRAFT_KEY]
    assert stored == {
        "original_slot_value": 64,
        "symbols": [casino.SEVEN, casino.SEVEN, casino.SEVEN],
        "payout_kind": "fixed",
        "payout_value": 250,
        "page": 2,
    }
    assert "7️⃣ · 7️⃣ · 7️⃣" in callback.message.edit_text.await_args.args[0]


async def test_reel_picker_changes_one_position_and_returns_to_editor() -> None:
    draft = _draft()
    state = AsyncMock()
    state.get_data.return_value = {admin.CASINO_DRAFT_KEY: draft}
    callback = _callback("adm:cpsym:1:s")

    await admin.cb_casino_payout_symbol(callback, state)

    assert draft["symbols"] == [casino.BAR, casino.SEVEN, casino.BAR]
    state.update_data.assert_awaited_once_with(**{admin.CASINO_DRAFT_KEY: draft})
    rendered = callback.message.edit_text.await_args.args[0]
    assert "BAR · 7️⃣ · BAR" in rendered


async def test_kind_switch_and_adjustments_clamp_to_selected_limit() -> None:
    draft = _draft(payout_value=100)
    state = AsyncMock()
    state.get_data.return_value = {admin.CASINO_DRAFT_KEY: draft}

    await admin.cb_casino_payout_kind(_callback("adm:cpkind:f"), state)
    assert (draft["payout_kind"], draft["payout_value"]) == ("fixed", 100)

    draft["payout_value"] = 4990
    callback = _callback("adm:cpadj:100")
    await admin.cb_casino_payout_adjust(callback, state)
    assert draft["payout_value"] == 5000
    rendered = callback.message.edit_text.await_args.args[0]
    assert "5000 см" in rendered


async def test_duplicate_save_keeps_editor_open_and_shows_alert(monkeypatch) -> None:
    draft = _draft(original_slot_value=None)
    state = AsyncMock()
    state.get_data.return_value = {admin.CASINO_DRAFT_KEY: draft}
    callback = _callback("adm:cpsave")
    save = AsyncMock(side_effect=casino.CasinoError("duplicate_payout_rule"))
    monkeypatch.setattr(admin.casino, "save_payout_rule", save)

    await admin.cb_casino_payout_save(callback, state)

    callback.answer.assert_awaited_once_with(texts.ADMIN_CASINO_RULE_DUPLICATE, show_alert=True)
    state.clear.assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()


async def test_save_persists_draft_clears_state_and_returns_to_page(monkeypatch) -> None:
    draft = _draft(
        symbols=[casino.BAR, casino.SEVEN, casino.BAR],
        payout_kind="fixed",
        payout_value=250,
        page=3,
    )
    state = AsyncMock()
    state.get_data.return_value = {admin.CASINO_DRAFT_KEY: draft}
    callback = _callback("adm:cpsave")
    save = AsyncMock()
    panel = AsyncMock(return_value=("список", SimpleNamespace()))
    monkeypatch.setattr(admin.casino, "save_payout_rule", save)
    monkeypatch.setattr(admin, "render_casino_payouts", panel)

    await admin.cb_casino_payout_save(callback, state)

    save.assert_awaited_once_with(
        original_slot_value=1,
        slot_value=casino.encode_slot((casino.BAR, casino.SEVEN, casino.BAR)),
        payout_kind="fixed",
        payout_value=250,
    )
    state.clear.assert_awaited_once()
    panel.assert_awaited_once_with(3)
    callback.message.edit_text.assert_awaited_once_with(
        "список", reply_markup=panel.return_value[1], parse_mode="HTML"
    )


async def test_delete_requires_confirmation_then_removes_original_rule(monkeypatch) -> None:
    draft = _draft(original_slot_value=64, symbols=[casino.BAR, casino.SEVEN, casino.BAR], page=1)
    state = AsyncMock()
    state.get_data.return_value = {admin.CASINO_DRAFT_KEY: draft}
    confirm = _callback("adm:cpdelask")

    await admin.cb_casino_payout_delete_confirm(confirm, state)

    assert "7️⃣ · 7️⃣ · 7️⃣" in confirm.message.edit_text.await_args.args[0]
    keyboard = confirm.message.edit_text.await_args.kwargs["reply_markup"]
    assert [button.callback_data for button in keyboard.inline_keyboard[0]] == [
        "adm:cpdelete",
        "adm:cpbackedit",
    ]

    delete = AsyncMock(return_value=True)
    panel = AsyncMock(return_value=("список", SimpleNamespace()))
    monkeypatch.setattr(admin.casino, "delete_payout_rule", delete)
    monkeypatch.setattr(admin, "render_casino_payouts", panel)
    callback = _callback("adm:cpdelete")

    await admin.cb_casino_payout_delete(callback, state)

    delete.assert_awaited_once_with(64)
    state.clear.assert_awaited_once()
    panel.assert_awaited_once_with(1)


async def test_stale_editor_callback_is_rejected_without_mutation() -> None:
    callback = _callback("adm:cpadj:1")
    state = AsyncMock()
    state.get_data.return_value = {}

    await admin.cb_casino_payout_adjust(callback, state)

    callback.answer.assert_awaited_once_with(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
    state.update_data.assert_not_awaited()
    callback.message.edit_text.assert_not_awaited()
