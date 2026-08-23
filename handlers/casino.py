"""Public Telegram slot-dice casino for group chats and forum topics."""

from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

import texts
from callbacks import CasinoCallback
from services import casino

router = Router()
logger = logging.getLogger(__name__)

SLOT_ANIMATION_SECONDS = 3.0


def _casino_callback(action: str, stake: int = 0) -> str:
    return CasinoCallback(action=action, stake=stake).pack()


def _spin_keyboard(stake: int) -> InlineKeyboardMarkup:
    """Public controls: every click charges the player who clicked."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.casino_repeat_button(stake),
                    callback_data=_casino_callback("r", stake),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_CASINO_OWN,
                    callback_data=_casino_callback("o"),
                ),
            ]
        ]
    )


def _saved_keyboard(stake: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.casino_repeat_button(stake),
                    callback_data=_casino_callback("r", stake),
                )
            ]
        ]
    )


def _thread_id(message: Message) -> int | None:
    if message.is_topic_message and message.message_thread_id is not None:
        return message.message_thread_id
    return None


def _error_text(error: casino.CasinoError) -> str:
    if error.code == "cooldown":
        return texts.casino_cooldown(error.retry_after)
    return {
        "bad_stake": texts.CASINO_BAD_STAKE,
        "no_player": texts.CASINO_NO_PLAYER,
        "locally_banned": texts.LOCAL_BANNED,
        "no_size": texts.CASINO_NO_SIZE,
        "insufficient": texts.CASINO_INSUFFICIENT,
        "disabled": texts.CASINO_DISABLED,
        "corp_frozen": texts.CASINO_RECOVERY,
        "bad_outcome": texts.CASINO_SPIN_CANCELED,
    }.get(error.code, texts.CASINO_SPIN_CANCELED)


async def _reject(
    message: Message,
    text: str,
    *,
    callback: CallbackQuery | None,
) -> None:
    if callback is not None:
        await callback.answer(text, show_alert=True)
    else:
        await message.answer(text)


async def _acknowledge(callback: CallbackQuery | None) -> None:
    """Best-effort callback ack after dice is visible or money is committed."""
    if callback is None:
        return
    try:
        await callback.answer()
    except TelegramAPIError:
        logger.warning("Could not acknowledge casino callback")


async def _send_notice(bot: Bot, message: Message, text: str) -> None:
    try:
        await bot.send_message(
            message.chat.id,
            text,
            parse_mode="HTML",
            message_thread_id=_thread_id(message),
        )
    except TelegramAPIError:
        logger.warning("Could not deliver casino notice")


async def _run_spin(
    message: Message,
    user: User,
    bot: Bot,
    *,
    stake: int | None,
    callback: CallbackQuery | None = None,
) -> None:
    thread_id = _thread_id(message)
    dice_message: Message | None = None

    async def supply_outcome() -> casino.SlotOutcome:
        nonlocal dice_message
        dice_message = await bot.send_dice(
            message.chat.id,
            emoji="🎰",
            message_thread_id=thread_id,
        )
        if dice_message.dice is None:
            raise casino.CasinoError("bad_outcome")
        return casino.SlotOutcome(dice_message.dice.value, dice_message.message_id)

    try:
        result = await casino.play(
            message.chat.id,
            user.id,
            supply_outcome,
            stake=stake,
        )
    except casino.CasinoError as error:
        if dice_message is None:
            await _reject(message, _error_text(error), callback=callback)
        else:
            logger.warning("Casino settlement rejected after visible dice: %s", error.code)
            await _acknowledge(callback)
            await _send_notice(bot, message, texts.CASINO_SPIN_CANCELED)
        return
    except TelegramAPIError:
        if dice_message is None:
            logger.warning("Telegram rejected casino dice send")
            await _reject(message, texts.CASINO_SEND_FAILED, callback=callback)
        else:
            logger.exception("Casino settlement failed after visible Telegram dice")
            await _acknowledge(callback)
            await _send_notice(bot, message, texts.CASINO_SPIN_CANCELED)
        return
    except Exception:
        logger.exception("Casino spin failed")
        await _acknowledge(callback)
        notice = texts.CASINO_SEND_FAILED if dice_message is None else texts.CASINO_SPIN_CANCELED
        await _send_notice(bot, message, notice)
        return

    await _acknowledge(callback)

    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=result.message_id,
            reply_markup=_spin_keyboard(result.stake),
        )
    except TelegramAPIError:
        # The spin is already committed; a missing convenience keyboard must not
        # turn a real result into a reported cancellation.
        logger.warning("Could not attach public controls to casino dice")

    # Settlement and all economy locks have completed before this presentation
    # delay. Let Telegram's animation finish before revealing the accounting.
    await asyncio.sleep(SLOT_ANIMATION_SECONDS)
    try:
        await bot.send_message(
            message.chat.id,
            texts.casino_result(user.id, user.first_name, result),
            parse_mode="HTML",
            message_thread_id=thread_id,
        )
    except TelegramAPIError:
        logger.warning("Could not deliver casino result")

    if result.bail_in is not None:
        await _send_notice(
            bot,
            message,
            texts.casino_bail_in_notice(
                user.first_name,
                result.gross_payout,
                result.bail_in.status,
            ),
        )


@router.message(Command("casino"))
async def cmd_casino(message: Message, command: CommandObject, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer(texts.CASINO_GROUP_ONLY)
        return

    raw = (command.args or "").strip()
    if raw:
        if re.fullmatch(r"[0-9]+", raw) is None:
            await message.answer(texts.CASINO_BAD_STAKE)
            return
        stake = int(raw)
        if not casino.MIN_STAKE <= stake <= casino.MAX_STAKE:
            await message.answer(texts.CASINO_BAD_STAKE)
            return
        try:
            saved = await casino.save_stake(message.chat.id, user.id, stake)
        except casino.CasinoError as error:
            await message.answer(_error_text(error))
            return
        await message.answer(
            texts.casino_stake_saved(saved),
            reply_markup=_saved_keyboard(saved),
            parse_mode="HTML",
        )
        return

    await _run_spin(message, user, bot, stake=None)


@router.callback_query(CasinoCallback.filter())
async def casino_callback(
    callback: CallbackQuery,
    callback_data: CasinoCallback,
    bot: Bot,
) -> None:
    message = callback.message
    if (
        not isinstance(message, Message)
        or message.chat.type not in {"group", "supergroup"}
        or callback.from_user is None
    ):
        await callback.answer(texts.CASINO_BUTTON_INVALID, show_alert=True)
        return

    if callback_data.action == "r":
        if not casino.MIN_STAKE <= callback_data.stake <= casino.MAX_STAKE:
            await callback.answer(texts.CASINO_BUTTON_INVALID, show_alert=True)
            return
        stake: int | None = callback_data.stake
    elif callback_data.action == "o" and callback_data.stake == 0:
        stake = None
    else:
        await callback.answer(texts.CASINO_BUTTON_INVALID, show_alert=True)
        return

    await _run_spin(message, callback.from_user, bot, stake=stake, callback=callback)


@router.callback_query(F.data.startswith("cas:"))
async def casino_bad_callback(callback: CallbackQuery) -> None:
    await callback.answer(texts.CASINO_BUTTON_INVALID, show_alert=True)
