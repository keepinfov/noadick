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


@pytest.mark.parametrize("raw", ["0", "51", "-1", "+5", "1.5", "5 10", "пять"])
async def test_casino_command_rejects_non_strict_stakes(raw: str) -> None:
    message = _message()

    await handler.cmd_casino(message, SimpleNamespace(args=raw), AsyncMock())

    message.answer.assert_awaited_once_with(texts.CASINO_BAD_STAKE)


async def test_casino_command_saves_without_spinning_or_buttons_and_shows_payouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _message()
    save = AsyncMock(return_value=17)
    count = AsyncMock(return_value=13)
    play = AsyncMock()
    monkeypatch.setattr(handler.casino, "save_stake", save)
    monkeypatch.setattr(handler.casino, "count_payout_rules", count)
    monkeypatch.setattr(handler.casino, "play", play)

    await handler.cmd_casino(message, SimpleNamespace(args="17"), AsyncMock())

    save.assert_awaited_once_with(message.chat.id, message.from_user.id, 17)
    count.assert_awaited_once_with()
    play.assert_not_awaited()
    message.answer.assert_awaited_once_with(
        texts.casino_stake_saved(17, 13),
        parse_mode="HTML",
    )
    value = message.answer.await_args.args[0]
    assert "<b>17 см</b>" in value
    assert "<b>13</b>" in value
    assert "×18" not in value and "×3" not in value and "×5" not in value


async def test_no_argument_spin_uses_saved_default_topic_and_waits_after_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _message(name="<Вася & Co>", topic=77)
    bot = AsyncMock()
    bot.send_dice.return_value = SimpleNamespace(
        dice=SimpleNamespace(value=64),
        message_id=777,
    )
    bot.me.return_value = SimpleNamespace(username="casino_test_bot")
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
    assert "reply_markup" not in result_call.kwargs
    assert result_call.args[1] == (
        "🎉 Чистыми <b>+170 см</b> · выплата <b>180 см</b> · "
        '<a href="https://t.me/casino_test_bot?start=casino_rules">'
        "Как считаются выигрыши</a>"
    )
    assert "\n" not in result_call.args[1]
    assert "<Вася & Co>" not in result_call.args[1]


async def test_loss_is_one_compact_line_without_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _message(name="Проигравший", topic=78)
    bot = AsyncMock()
    bot.send_dice.return_value = SimpleNamespace(
        dice=SimpleNamespace(value=2),
        message_id=778,
    )
    bot.me.return_value = SimpleNamespace(username="casino_test_bot")

    async def play(_chat_id, _user_id, supplier, *, stake=None):
        await supplier()
        return _result(
            value=2,
            message_id=778,
            symbols=(casino.GRAPES, casino.BAR, casino.BAR),
            multiplier=0,
            gross_payout=0,
            net=-10,
            size_after=90,
            corporation_balance=10,
            deficit=0,
        )

    monkeypatch.setattr(handler.casino, "play", play)
    sleep = AsyncMock()
    monkeypatch.setattr(handler.asyncio, "sleep", sleep)

    await handler.cmd_casino(message, SimpleNamespace(args=None), bot)

    sleep.assert_awaited_once_with(3.0)
    bot.send_message.assert_awaited_once_with(
        message.chat.id,
        texts.casino_loss(10, "https://t.me/casino_test_bot?start=casino_rules"),
        parse_mode="HTML",
        message_thread_id=78,
    )
    loss_text = bot.send_message.await_args.args[1]
    assert loss_text == (
        '💸 Сняли <b>10 см</b> · <a href="https://t.me/casino_test_bot?start=casino_rules">'
        "Как считаются выигрыши</a>"
    )
    assert "\n" not in loss_text
    assert "Проигравший" not in loss_text


async def test_fixed_positive_payout_uses_result_line_even_with_zero_multiplier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _message(topic=79)
    bot = AsyncMock()
    bot.send_dice.return_value = SimpleNamespace(dice=SimpleNamespace(value=2), message_id=779)
    bot.me.return_value = SimpleNamespace(username="casino_test_bot")

    async def play(_chat_id, _user_id, supplier, *, stake=None):
        await supplier()
        return _result(
            value=2,
            message_id=779,
            symbols=(casino.GRAPES, casino.BAR, casino.BAR),
            multiplier=0,
            payout_kind="fixed",
            payout_value=7,
            gross_payout=7,
            net=-3,
            size_after=97,
            corporation_balance=3,
            deficit=0,
        )

    monkeypatch.setattr(handler.casino, "play", play)
    monkeypatch.setattr(handler.asyncio, "sleep", AsyncMock())

    await handler.cmd_casino(message, SimpleNamespace(args=None), bot)

    result_text = bot.send_message.await_args.args[1]
    assert result_text.startswith("🎉 Чистыми <b>−3 см</b> · выплата <b>7 см</b>")
    assert "💸 Сняли" not in result_text


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
    bot.me.return_value = SimpleNamespace(username="casino_test_bot")

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
    assert "reply_markup" not in result_call.kwargs


def test_casino_result_and_bail_in_notice_escape_names_and_hide_deposits() -> None:
    rules_url = 'https://t.me/casino_bot?start=casino_rules&unsafe="<tag>'
    value = texts.casino_result(_result(), rules_url)
    loss = texts.casino_loss(10, rules_url)
    notice = texts.casino_bail_in_notice("<Вася & Co>", 180, "recovery")
    unknown = texts.casino_bail_in_notice("Игрок", 10, "<broken>")

    assert value.startswith("🎉 Чистыми <b>+170 см</b>")
    assert "выплата <b>180 см</b>" in value.lower()
    assert "\n" not in value and "\n" not in loss
    assert "&amp;unsafe=&quot;&lt;tag&gt;" in value
    assert "&amp;unsafe=&quot;&lt;tag&gt;" in loss
    assert "&lt;Вася &amp; Co&gt;" in notice
    assert "Статус кассы: <b>восстановление</b>" in notice
    assert "recovery" not in notice
    assert "списано" not in notice
    assert "защищ" not in notice
    assert "<broken>" not in unknown
    assert "<b>неизвестный</b>" in unknown


async def test_casino_rules_deep_link_uses_current_database_rules(monkeypatch) -> None:
    message = _message(chat_type="private")
    rules = [
        SimpleNamespace(slot_value=1, payout_kind="multiplier", payout_value=5),
        SimpleNamespace(slot_value=2, payout_kind="fixed", payout_value=7),
    ]
    monkeypatch.setattr(handler.casino, "list_payout_rules", AsyncMock(return_value=rules))

    await handler.casino_rules_deep_link(message)

    message.answer.assert_awaited_once()
    rules = message.answer.await_args.args[0]
    assert "BAR · BAR · BAR — ×5" in rules
    assert "🍇 · BAR · BAR — 7 см" in rules
    assert "×18" not in rules
    assert "Чистый итог = выплата − ставка" in rules


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
