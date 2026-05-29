from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message

from repositories import threads as threads_repo

router = Router()


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in {"creator", "administrator"}


@router.message(Command("setbcast"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_setbcast(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if not await _is_chat_admin(bot, message.chat.id, user.id):
        await message.answer("Только администратор чата может задать тему рассылки.")
        return
    if not message.is_topic_message or message.message_thread_id is None:
        await message.answer(
            "Запусти эту команду внутри нужной темы форума — "
            "именно туда будут приходить рассылки."
        )
        return
    await threads_repo.set_default_thread(message.chat.id, message.message_thread_id)
    await message.answer("✅ Эта тема выбрана для рассылок.")


@router.message(Command("unsetbcast"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_unsetbcast(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if not await _is_chat_admin(bot, message.chat.id, user.id):
        await message.answer("Только администратор чата может изменить тему рассылки.")
        return
    if await threads_repo.clear_default_thread(message.chat.id):
        await message.answer(
            "Тема рассылки сброшена. Теперь будет использоваться самая активная тема."
        )
    else:
        await message.answer("Тема рассылки и так не была задана.")
