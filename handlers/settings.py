from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

import texts
from repositories import threads as threads_repo
from services.chat_admin import is_chat_admin

router = Router()


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
