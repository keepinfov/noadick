"""Outer middleware: keep a live registry of chats/users, relink legacy data,
and enforce bans. Runs on every message and callback query."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from repositories import chats as chats_repo
from repositories import threads as threads_repo
from services.admins import is_global_admin
from services.registry import chat_hash, relink_legacy


def _extract(event: Any) -> tuple[Chat | None, User | None]:
    if isinstance(event, Message):
        return event.chat, event.from_user
    if isinstance(event, CallbackQuery):
        chat = event.message.chat if event.message else None
        return chat, event.from_user
    return None, None


class RegistryMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._bot_username: str | None = None

    async def _bot_link(self, bot: Bot) -> str:
        if self._bot_username is None:
            me = await bot.me()
            self._bot_username = me.username or ""
        return f"https://t.me/{self._bot_username}?start=go"

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        chat, user = _extract(event)

        if user is not None:
            db_user = await chats_repo.upsert_user(
                user.id, user.first_name or "", user.username
            )
            if db_user.is_banned and not is_global_admin(user.id):
                return None

        if chat is not None:
            db_chat = await chats_repo.upsert_chat(
                chat.id, chat.title or "", chat.type or "", chat_hash(chat.id)
            )
            if db_chat.is_banned:
                return None
            await relink_legacy(chat.id)

        if (
            isinstance(event, Message)
            and event.is_topic_message
            and event.message_thread_id is not None
        ):
            await threads_repo.bump_thread(event.chat.id, event.message_thread_id)

        # DM gate: a command in a group is ignored until the user has opened a
        # private chat with the bot (so the bot can later DM them about
        # broadcast blocks, bans, support, etc.). Global admins are exempt.
        if (
            isinstance(event, Message)
            and event.text
            and event.text.startswith("/")
            and chat is not None
            and chat.type != "private"
            and user is not None
            and not is_global_admin(user.id)
        ):
            if await chats_repo.get_chat(user.id) is None:
                bot: Bot = data["bot"]
                link = await self._bot_link(bot)
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✍️ Написать боту", url=link)]
                    ]
                )
                try:
                    await event.reply(
                        "👋 Чтобы пользоваться ботом, сначала напишите ему в личку "
                        "(кнопка ниже, затем /start). Это нужно, чтобы бот мог "
                        "уведомлять вас о блокировках рассылок и обращениях в "
                        "поддержку.",
                        reply_markup=kb,
                    )
                except Exception:
                    pass
                return None

        return await handler(event, data)
