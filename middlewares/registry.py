"""Outer middleware: keep a live registry of chats/users, relink legacy data,
and enforce bans. Runs on every message and callback query."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Chat, Message, User

from repositories import chats as chats_repo
from services.registry import chat_hash, relink_legacy


def _extract(event: Any) -> tuple[Chat | None, User | None]:
    if isinstance(event, Message):
        return event.chat, event.from_user
    if isinstance(event, CallbackQuery):
        chat = event.message.chat if event.message else None
        return chat, event.from_user
    return None, None


class RegistryMiddleware(BaseMiddleware):
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
            if db_user.is_banned:
                return None

        if chat is not None:
            db_chat = await chats_repo.upsert_chat(
                chat.id, chat.title or "", chat.type or "", chat_hash(chat.id)
            )
            if db_chat.is_banned:
                return None
            await relink_legacy(chat.id)

        return await handler(event, data)
