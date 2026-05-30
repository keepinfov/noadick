"""Shared local-admin (chat-level) authorization.

A "local admin" is a Telegram chat owner/administrator, distinct from a GLOBAL
admin (ADMIN_IDS env). Used to gate per-chat moderation and configuration
commands. Fail-closed: any API error denies access.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import Message


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in {"creator", "administrator"}


class IsChatAdmin(BaseFilter):
    """Filter passing only group/supergroup messages from a chat admin."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        if message.chat.type not in {"group", "supergroup"}:
            return False
        user = message.from_user
        if user is None:
            return False
        return await is_chat_admin(bot, message.chat.id, user.id)
