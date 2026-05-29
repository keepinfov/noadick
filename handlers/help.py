from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

router = Router()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "Команды:\n"
        "/help — вывести этот текст\n"
        "/dick — испытать удачу\n"
        "/duel [ставка] — вызвать на дуэль (ответом на сообщение)\n"
        "/me — твой профиль и статистика\n"
        "/top — вывести топ список\n"
        "/ping — ping-pong"
    )
    await message.answer(text)
