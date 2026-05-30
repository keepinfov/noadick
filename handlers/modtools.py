"""Local-admin (chat-level) moderation and per-chat configuration.

Plain slash commands gated by IsChatAdmin (chat owner/administrator), distinct
from the global /admin panel. Available only in groups/supergroups.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import texts
from handlers.replies import reply_target
from repositories import players as players_repo
from repositories.players import get_chat_lock
from services.chat_admin import IsChatAdmin
from services.settings import SettingError, get_effective, set_setting

router = Router()


async def _resolve_target(
    message: Message, command: CommandObject
) -> tuple[int, str] | None:
    """Target player from a reply or a numeric user id argument."""
    target = reply_target(message)
    if target is not None:
        return target.id, target.first_name or str(target.id)
    arg = (command.args or "").strip()
    if arg.isdigit():
        uid = int(arg)
        p = await players_repo.get_player(message.chat.id, uid)
        name = p.name if p and p.name else str(uid)
        return uid, name
    return None


@router.message(Command("localban"), IsChatAdmin())
async def cmd_localban(message: Message, command: CommandObject) -> None:
    resolved = await _resolve_target(message, command)
    if resolved is None:
        await message.answer(texts.MOD_NEED_TARGET, parse_mode="HTML")
        return
    uid, name = resolved
    chat_id = message.chat.id
    async with get_chat_lock(chat_id):
        p = await players_repo.get_player(chat_id, uid)
        if p is not None:
            name = p.name or name
            if p.is_chat_banned:
                await message.answer(texts.mod_localban_already(name), parse_mode="HTML")
                return
        await players_repo.set_player_fields(chat_id, uid, is_chat_banned=True)
    await message.answer(texts.mod_localban_done(name), parse_mode="HTML")


@router.message(Command("localunban"), IsChatAdmin())
async def cmd_localunban(message: Message, command: CommandObject) -> None:
    resolved = await _resolve_target(message, command)
    if resolved is None:
        await message.answer(texts.MOD_NEED_TARGET, parse_mode="HTML")
        return
    uid, name = resolved
    chat_id = message.chat.id
    async with get_chat_lock(chat_id):
        p = await players_repo.get_player(chat_id, uid)
        if p is None:
            await message.answer(texts.MOD_TARGET_NOT_FOUND, parse_mode="HTML")
            return
        name = p.name or name
        if not p.is_chat_banned:
            await message.answer(texts.mod_localunban_already(name), parse_mode="HTML")
            return
        await players_repo.set_player_fields(chat_id, uid, is_chat_banned=False)
    await message.answer(texts.mod_localunban_done(name), parse_mode="HTML")


@router.message(Command("resetleaderboard"), IsChatAdmin())
async def cmd_resetleaderboard(message: Message) -> None:
    chat_id = message.chat.id
    async with get_chat_lock(chat_id):
        count = await players_repo.zero_chat_sizes(chat_id)
    await message.answer(texts.mod_reset_done(count))


@router.message(Command("gameconfig"), IsChatAdmin())
async def cmd_gameconfig(message: Message, command: CommandObject) -> None:
    chat_id = message.chat.id
    parts = (command.args or "").split(maxsplit=1)

    if not parts or not parts[0].strip():
        eff = await get_effective(chat_id)
        await message.answer(
            texts.gameconfig_current(
                eff.tz, eff.diseases_enabled, eff.duel_stake_default, eff.duel_timeout
            ),
            parse_mode="HTML",
        )
        return

    key = parts[0].strip()
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(texts.GAMECONFIG_USAGE, parse_mode="HTML")
        return

    value = parts[1].strip()
    try:
        await set_setting(chat_id, key, value)
    except SettingError as exc:
        code = str(exc).split(":", 1)[0]
        msg = texts.GAMECONFIG_ERRORS.get(code, texts.GAMECONFIG_ERRORS["unknown_key"])
        await message.answer(msg, parse_mode="HTML")
        return

    await message.answer(texts.gameconfig_set_ok(key, value), parse_mode="HTML")
