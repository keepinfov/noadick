from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, SendDice
from aiogram.types import Message

import texts
from callbacks import CasinoCallback, SettingsCallback
from handlers import casino as handler
from services import casino


def _message(
    *,
    user_id: int = 71,
    name: str = "Игрок",
    chat_type: str = "supergroup",
    topic: int | None = None,
):
    return SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type=chat_type),
        from_user=SimpleNamespace(id=user_id, first_name=name),
        is_topic_message=topic is not None,
        message_thread_id=topic,
        answer=AsyncMock(),
    )


def _callback_message(*, chat_type: str = "supergroup", topic: int | None = None):
    message = MagicMock(spec=Message)
    message.chat = SimpleNamespace(id=-1007001, type=chat_type)
    message.is_topic_message = topic is not None
    message.message_thread_id = topic
    return message


def _result(**overrides) -> casino.CasinoResult:
    values = {
        "stake": 10,
        "value": 64,
        "message_id": 777,
        "symbols": (casino.SEVEN, casino.SEVEN, casino.SEVEN),
        "multiplier": 18,
        "gross_payout": 180,
        "net": 170,
        "size_after": 270,
        "corporation_balance": -170,
        "deficit": 170,
        "bail_in": None,
    }
    values.update(overrides)
    return casino.CasinoResult(**values)


def test_public_spin_keyboard_has_repeat_and_clickers_default() -> None:
    keyboard = handler._spin_keyboard(50)
    buttons = keyboard.inline_keyboard[0]
    payloads = [button.callback_data for button in buttons]

    assert [button.text for button in buttons] == ["🎰 Крутить 50", "🎯 Крутить свою"]
    assert all(payload is not None and len(payload.encode()) <= 64 for payload in payloads)
    repeat = CasinoCallback.unpack(payloads[0])
    own = CasinoCallback.unpack(payloads[1])
    assert (repeat.action, repeat.stake) == ("r", 50)
    assert (own.action, own.stake) == ("o", 0)


@pytest.mark.parametrize("raw", ["0", "51", "-1", "+5", "1.5", "5 10", "пять"])
async def test_casino_command_rejects_non_strict_stakes(raw: str) -> None:
    message = _message()

    await handler.cmd_casino(message, SimpleNamespace(args=raw), AsyncMock())

    message.answer.assert_awaited_once_with(texts.CASINO_BAD_STAKE)


async def test_casino_command_saves_without_spinning_and_confirms_with_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _message()
    save = AsyncMock(return_value=17)
    play = AsyncMock()
    monkeypatch.setattr(handler.casino, "save_stake", save)
    monkeypatch.setattr(handler.casino, "play", play)

    await handler.cmd_casino(message, SimpleNamespace(args="17"), AsyncMock())

    save.assert_awaited_once_with(message.chat.id, message.from_user.id, 17)
    play.assert_not_awaited()
    markup = message.answer.await_args.kwargs["reply_markup"]
    payload = markup.inline_keyboard[0][0].callback_data
    assert CasinoCallback.unpack(payload) == CasinoCallback(action="r", stake=17)
    assert "<b>17 см</b>" in message.answer.await_args.args[0]


async def test_no_argument_spin_uses_saved_default_topic_and_waits_after_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _message(name="<Вася & Co>", topic=77)
    bot = AsyncMock()
    bot.send_dice.return_value = SimpleNamespace(
        dice=SimpleNamespace(value=64),
        message_id=777,
    )
    timeline: list[str] = []

    async def play(chat_id, user_id, supplier, *, stake=None):
        assert (chat_id, user_id, stake) == (message.chat.id, message.from_user.id, None)
        outcome = await supplier()
        assert outcome == casino.SlotOutcome(64, 777)
        timeline.append("settled")
        return _result()

    async def sleep(seconds):
        assert seconds == 3.0
        timeline.append("sleep")

    async def send_message(*_args, **_kwargs):
        timeline.append("result")

    monkeypatch.setattr(handler.casino, "play", play)
    monkeypatch.setattr(handler.asyncio, "sleep", sleep)
    bot.send_message.side_effect = send_message

    await handler.cmd_casino(message, SimpleNamespace(args=None), bot)

    bot.send_dice.assert_awaited_once_with(
        message.chat.id,
        emoji="🎰",
        message_thread_id=77,
    )
    assert timeline == ["settled", "sleep", "result"]
    bot.edit_message_reply_markup.assert_not_awaited()
    result_call = bot.send_message.await_args
    assert result_call.kwargs["message_thread_id"] == 77
    result_buttons = result_call.kwargs["reply_markup"].inline_keyboard[0]
    assert [button.text for button in result_buttons] == [
        "🎰 Крутить 10",
        "🎯 Крутить свою",
    ]
    assert "&lt;Вася &amp; Co&gt;" in result_call.args[1]
    assert "<Вася & Co>" not in result_call.args[1]


async def test_public_callback_charges_clicker_and_preserves_default_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _callback_message()
    callback = SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=222, first_name="Другой"),
        answer=AsyncMock(),
    )
    run = AsyncMock()
    monkeypatch.setattr(handler, "_run_spin", run)
    bot = AsyncMock()

    await handler.casino_callback(callback, CasinoCallback(action="r", stake=12), bot)
    await handler.casino_callback(callback, CasinoCallback(action="o"), bot)

    first, second = run.await_args_list
    assert first.args[:3] == (message, callback.from_user, bot)
    assert first.kwargs == {"stake": 12, "callback": callback}
    assert second.args[:3] == (message, callback.from_user, bot)
    assert second.kwargs == {"stake": None, "callback": callback}


@pytest.mark.parametrize(
    "callback_data",
    [
        CasinoCallback(action="x", stake=5),
        CasinoCallback(action="r", stake=0),
        CasinoCallback(action="r", stake=51),
        CasinoCallback(action="o", stake=5),
    ],
)
async def test_invalid_typed_casino_callbacks_are_rejected(callback_data) -> None:
    callback = SimpleNamespace(
        message=_callback_message(),
        from_user=SimpleNamespace(id=1, first_name="A"),
        answer=AsyncMock(),
    )

    await handler.casino_callback(callback, callback_data, AsyncMock())

    callback.answer.assert_awaited_once_with(texts.CASINO_BUTTON_INVALID, show_alert=True)


async def test_inaccessible_and_private_casino_callbacks_are_rejected() -> None:
    inaccessible = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(type="supergroup")),
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    await handler.casino_callback(inaccessible, CasinoCallback(action="o"), AsyncMock())
    inaccessible.answer.assert_awaited_once_with(texts.CASINO_BUTTON_INVALID, show_alert=True)

    private = SimpleNamespace(
        message=_callback_message(chat_type="private"),
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    await handler.casino_callback(private, CasinoCallback(action="o"), AsyncMock())
    private.answer.assert_awaited_once_with(texts.CASINO_BUTTON_INVALID, show_alert=True)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("no_player", texts.CASINO_NO_PLAYER),
        ("locally_banned", texts.LOCAL_BANNED),
        ("no_size", texts.CASINO_NO_SIZE),
        ("insufficient", texts.CASINO_INSUFFICIENT),
        ("disabled", texts.CASINO_DISABLED),
        ("corp_frozen", texts.CASINO_RECOVERY),
    ],
)
async def test_preflight_errors_are_clear_callback_alerts(
    monkeypatch: pytest.MonkeyPatch, code: str, expected: str
) -> None:
    message = _callback_message()
    callback = SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=1, first_name="A"),
        answer=AsyncMock(),
    )
    bot = AsyncMock()
    monkeypatch.setattr(
        handler.casino,
        "play",
        AsyncMock(side_effect=casino.CasinoError(code)),
    )

    await handler._run_spin(
        message,
        callback.from_user,
        bot,
        stake=None,
        callback=callback,
    )

    callback.answer.assert_awaited_once_with(expected, show_alert=True)
    bot.send_dice.assert_not_awaited()


async def test_cooldown_alert_includes_retry_time(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _callback_message()
    callback = SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=1, first_name="A"),
        answer=AsyncMock(),
    )
    monkeypatch.setattr(
        handler.casino,
        "play",
        AsyncMock(side_effect=casino.CasinoError("cooldown", retry_after=3)),
    )

    await handler._run_spin(
        message,
        callback.from_user,
        AsyncMock(),
        stake=None,
        callback=callback,
    )

    callback.answer.assert_awaited_once_with(texts.casino_cooldown(3), show_alert=True)


async def test_send_dice_failure_reports_no_charge(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _message()
    bot = AsyncMock()
    bot.send_dice.side_effect = TelegramBadRequest(
        method=SendDice(chat_id=message.chat.id, emoji="🎰"),
        message="dice unavailable",
    )

    async def play(_chat_id, _user_id, supplier, *, stake=None):
        return await supplier()

    monkeypatch.setattr(handler.casino, "play", play)

    await handler._run_spin(message, message.from_user, bot, stake=None)

    message.answer.assert_awaited_once_with(texts.CASINO_SEND_FAILED)
    bot.edit_message_reply_markup.assert_not_awaited()


async def test_settlement_failure_after_visible_dice_reports_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _callback_message(topic=91)
    callback = SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=71, first_name="Игрок"),
        answer=AsyncMock(
            side_effect=TelegramBadRequest(
                method=AnswerCallbackQuery(callback_query_id="expired"),
                message="query is too old",
            )
        ),
    )
    bot = AsyncMock()
    bot.send_dice.return_value = SimpleNamespace(
        dice=SimpleNamespace(value=64),
        message_id=333,
    )

    async def play(_chat_id, _user_id, supplier, *, stake=None):
        await supplier()
        raise RuntimeError("settlement exploded")

    monkeypatch.setattr(handler.casino, "play", play)

    await handler._run_spin(
        message,
        callback.from_user,
        bot,
        stake=None,
        callback=callback,
    )

    callback.answer.assert_awaited_once_with()
    bot.send_message.assert_awaited_once_with(
        message.chat.id,
        texts.CASINO_SPIN_CANCELED,
        parse_mode="HTML",
        message_thread_id=91,
    )
    bot.edit_message_reply_markup.assert_not_awaited()


async def test_expired_callback_after_commit_does_not_hide_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _callback_message()
    callback = SimpleNamespace(
        message=message,
        from_user=SimpleNamespace(id=71, first_name="Игрок"),
        answer=AsyncMock(
            side_effect=TelegramBadRequest(
                method=AnswerCallbackQuery(callback_query_id="expired"),
                message="query is too old",
            )
        ),
    )
    bot = AsyncMock()
    bot.send_dice.return_value = SimpleNamespace(
        dice=SimpleNamespace(value=64),
        message_id=777,
    )

    async def play(_chat_id, _user_id, supplier, *, stake=None):
        await supplier()
        return _result()

    monkeypatch.setattr(handler.casino, "play", play)
    sleep = AsyncMock()
    monkeypatch.setattr(handler.asyncio, "sleep", sleep)

    await handler._run_spin(
        message,
        callback.from_user,
        bot,
        stake=10,
        callback=callback,
    )

    callback.answer.assert_awaited_once_with()
    bot.edit_message_reply_markup.assert_not_awaited()
    sleep.assert_awaited_once_with(3.0)
    bot.send_message.assert_awaited_once()
    result_call = bot.send_message.await_args
    assert result_call.kwargs["reply_markup"] == handler._spin_keyboard(10)


def test_casino_result_and_bail_in_notice_escape_names_and_hide_deposits() -> None:
    value = texts.casino_result(7, "<Вася & Co>", _result())
    notice = texts.casino_bail_in_notice("<Вася & Co>", 180, "recovery")
    unknown = texts.casino_bail_in_notice("Игрок", 10, "<broken>")

    assert "&lt;Вася &amp; Co&gt;" in value
    assert "7️⃣ · 7️⃣ · 7️⃣" in value
    assert "ставка: <b>10 см</b>" in value.lower()
    assert "выплата: <b>180 см</b>" in value.lower()
    assert "&lt;Вася &amp; Co&gt;" in notice
    assert "Статус кассы: <b>восстановление</b>" in notice
    assert "recovery" not in notice
    assert "списано" not in notice
    assert "защищ" not in notice
    assert "<broken>" not in unknown
    assert "<b>неизвестный</b>" in unknown


async def test_settings_panel_exposes_authorized_casino_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from handlers import settings as settings_handler
    from services import settings_view

    effective = SimpleNamespace(
        tz="UTC",
        diseases_enabled=True,
        banking_enabled=True,
        poker_enabled=True,
        casino_enabled=False,
        duel_stake_default=5,
        duel_timeout=60,
        stats_digest_enabled=False,
        stats_digest_weekday=0,
        stats_digest_hour=10,
    )
    keyboard = settings_view.settings_kb(-1007001, effective, scope="local")
    casino_buttons = [
        button
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("settings:casino:")
    ]
    assert len(casino_buttons) == 1
    assert casino_buttons[0].text == "🎰 Казино: ❌"

    callback = SimpleNamespace(
        message=_callback_message(),
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    monkeypatch.setattr(settings_handler, "_may_edit_settings", AsyncMock(return_value=True))
    toggle = AsyncMock(return_value=True)
    monkeypatch.setattr(settings_handler.settings, "toggle_casino", toggle)
    rerender = AsyncMock()
    monkeypatch.setattr(settings_handler, "_rerender", rerender)

    data = SettingsCallback(action="casino", chat_id=-1007001)
    await settings_handler.cb_st_toggle_casino(callback, AsyncMock(), data)

    toggle.assert_awaited_once_with(-1007001)
    rerender.assert_awaited_once_with(callback, -1007001)


async def test_gameconfig_current_shows_casino_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from handlers import modtools

    effective = SimpleNamespace(
        tz="UTC",
        diseases_enabled=True,
        poker_enabled=True,
        casino_enabled=False,
        duel_stake_default=5,
        duel_timeout=60,
    )
    monkeypatch.setattr(modtools, "get_effective", AsyncMock(return_value=effective))
    message = _message()

    await modtools.cmd_gameconfig(message, SimpleNamespace(args=None))

    assert "casino: <code>off</code>" in message.answer.await_args.args[0]
