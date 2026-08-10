from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import texts
from callbacks import SettingsCallback
from repositories import threads as threads_repo
from services import settings, settings_view
from services.admins import is_global_admin
from services.chat_admin import IsChatAdmin, is_chat_admin

router = Router()
logger = logging.getLogger(__name__)


class SettingsStates(StatesGroup):
    set_tz = State()


@router.message(Command("setbcast"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_setbcast(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if not await is_chat_admin(bot, message.chat.id, user.id):
        await message.answer(texts.BCAST_NOT_ADMIN)
        return
    if not message.is_topic_message or message.message_thread_id is None:
        await message.answer(texts.BCAST_NEED_TOPIC)
        return
    await threads_repo.set_default_thread(message.chat.id, message.message_thread_id)
    await message.answer(texts.BCAST_SET)


@router.message(Command("unsetbcast"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_unsetbcast(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if not await is_chat_admin(bot, message.chat.id, user.id):
        await message.answer(texts.BCAST_NOT_ADMIN)
        return
    if await threads_repo.clear_default_thread(message.chat.id):
        await message.answer(texts.BCAST_CLEARED)
    else:
        await message.answer(texts.BCAST_NOT_SET)


# ---- per-chat settings panel (local admins via /settings) ----


@router.message(Command("settings"), IsChatAdmin())
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await settings_view.render_settings(message.chat.id, scope="local")
    await message.answer(text, reply_markup=kb)


def _scope(callback: CallbackQuery) -> str:
    """Global admins drive the panel from their private chat with the bot; local
    admins from the group. The chat type tells the two apart and decides whether
    Back returns to the global panel or a Close button is shown."""
    chat = callback.message.chat if callback.message else None
    if chat is not None and chat.type == "private":
        return "global"
    return "local"


async def _may_edit_settings(callback: CallbackQuery, bot: Bot, chat_id: int) -> bool:
    """Global admins may edit any chat; a local admin only their own chat."""
    user = callback.from_user
    if user is None:
        return False
    if is_global_admin(user.id):
        return True
    chat = callback.message.chat if callback.message else None
    if chat is None or chat.type not in {"group", "supergroup"}:
        return False
    if chat_id != chat.id:
        return False
    return await is_chat_admin(bot, chat.id, user.id)


async def _rerender(callback: CallbackQuery, chat_id: int) -> None:
    text, kb = await settings_view.render_settings(chat_id, scope=_scope(callback))
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest as e:
            # Re-clicking an already-active option re-renders identical content;
            # Telegram's "message is not modified" is benign here.
            if "message is not modified" not in str(e).lower():
                raise
    await callback.answer()


@router.callback_query(F.data == "st:noop")
async def cb_st_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "st:close")
async def cb_st_close(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    chat = callback.message.chat if callback.message else None
    user = callback.from_user
    allowed = bool(user and is_global_admin(user.id))
    if chat is not None and chat.type in {"group", "supergroup"} and user is not None:
        allowed = await is_chat_admin(bot, chat.id, user.id)
    if not allowed:
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await state.clear()
    if callback.message is not None:
        try:
            await callback.message.delete()
        except TelegramAPIError:
            logger.info("Could not delete settings panel")
    await callback.answer()


@router.callback_query(SettingsCallback.filter(F.action == "diseases"))
@router.callback_query(F.data.startswith("st:tgl:dis:"))
async def cb_st_toggle_diseases(
    callback: CallbackQuery, bot: Bot, callback_data: SettingsCallback | None = None
) -> None:
    try:
        chat_id = callback_data.chat_id if callback_data else int(callback.data.split(":")[3])
    except (AttributeError, IndexError, ValueError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await settings.toggle_diseases(chat_id)
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action == "banking"))
@router.callback_query(F.data.startswith("st:tgl:bank:"))
async def cb_st_toggle_banking(
    callback: CallbackQuery, bot: Bot, callback_data: SettingsCallback | None = None
) -> None:
    try:
        chat_id = callback_data.chat_id if callback_data else int(callback.data.split(":")[3])
    except (AttributeError, IndexError, ValueError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await settings.toggle_banking(chat_id)
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action == "poker"))
async def cb_st_toggle_poker(
    callback: CallbackQuery, bot: Bot, callback_data: SettingsCallback
) -> None:
    chat_id = callback_data.chat_id
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await settings.toggle_poker(chat_id)
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action == "digest"))
async def cb_st_toggle_digest(
    callback: CallbackQuery, bot: Bot, callback_data: SettingsCallback
) -> None:
    chat_id = callback_data.chat_id
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await settings.toggle_stats_digest(chat_id)
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action.in_({"dweekday", "dhour"})))
async def cb_st_adjust_digest(
    callback: CallbackQuery, bot: Bot, callback_data: SettingsCallback
) -> None:
    chat_id = callback_data.chat_id
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    try:
        delta = int(callback_data.value)
        key = "weekday" if callback_data.action == "dweekday" else "hour"
        await settings.adjust_stats_digest(chat_id, key, delta)
    except (ValueError, settings.SettingError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action.in_({"stake", "timeout"})))
@router.callback_query(F.data.startswith("st:adj:"))
async def cb_st_adjust(
    callback: CallbackQuery, bot: Bot, callback_data: SettingsCallback | None = None
) -> None:
    try:
        if callback_data:
            what, chat_id, delta = (
                callback_data.action,
                callback_data.chat_id,
                int(callback_data.value),
            )
        else:
            _, _, legacy_what, chat_id_s, delta_s = callback.data.split(":")
            if legacy_what not in {"stake", "timeout"}:
                raise ValueError("unknown setting")
            what = legacy_what
            chat_id, delta = int(chat_id_s), int(delta_s)
    except (AttributeError, ValueError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    key = "duel_stake" if what == "stake" else "duel_timeout"
    try:
        await settings.adjust(chat_id, key, delta)
    except settings.SettingError:
        await callback.answer()
        return
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action == "show"))
@router.callback_query(F.data.startswith("st:show:"))
async def cb_st_show(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    callback_data: SettingsCallback | None = None,
) -> None:
    # Re-open the settings panel (e.g. cancelling the TZ text prompt).
    try:
        chat_id = callback_data.chat_id if callback_data else int(callback.data.split(":")[2])
    except (AttributeError, IndexError, ValueError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await state.set_state(None)
    await _rerender(callback, chat_id)


@router.callback_query(SettingsCallback.filter(F.action == "timezone"))
@router.callback_query(F.data.startswith("st:tz:"))
async def cb_st_tz(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    callback_data: SettingsCallback | None = None,
) -> None:
    try:
        chat_id = callback_data.chat_id if callback_data else int(callback.data.split(":")[2])
    except (AttributeError, IndexError, ValueError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    if not await _may_edit_settings(callback, bot, chat_id):
        await callback.answer(texts.SETTINGS_NOT_ALLOWED, show_alert=True)
        return
    await state.set_state(SettingsStates.set_tz)
    await state.update_data(tz_chat_id=chat_id, tz_scope=_scope(callback))
    if callback.message is not None:
        # Always offer a way out of the text-input prompt.
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.BTN_CANCEL,
                        callback_data=SettingsCallback(action="show", chat_id=chat_id).pack(),
                    )
                ]
            ]
        )
        await callback.message.edit_text(texts.SETTINGS_ENTER_TZ, reply_markup=cancel_kb)
    await callback.answer()


@router.message(SettingsStates.set_tz)
async def msg_set_tz(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("tz_chat_id")
    scope = data.get("tz_scope", "local")
    if chat_id is None:
        await state.clear()
        return
    value = (message.text or "").strip()
    try:
        await settings.set_setting(chat_id, "tz", value)
    except settings.SettingError:
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.BTN_CANCEL,
                        callback_data=SettingsCallback(action="show", chat_id=chat_id).pack(),
                    )
                ]
            ]
        )
        await message.answer(
            f"{texts.SETTINGS_BAD_TZ}\n\n{texts.SETTINGS_ENTER_TZ}", reply_markup=cancel_kb
        )
        return
    await state.clear()
    text, kb = await settings_view.render_settings(chat_id, scope=scope)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
