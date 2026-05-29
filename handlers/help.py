from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Это бот-игра.\n\n"
        "Теперь бот может писать тебе в личку — например, чтобы предупредить о "
        "блокировке рассылки или ответить по обращению в поддержку.\n\n"
        "Добавь меня в групповой чат и отправь /help, чтобы увидеть список команд."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "Команды:\n"
        "/help — вывести этот текст\n"
        "/dick — испытать удачу\n"
        "/duel [ставка] — вызвать на дуэль (ответом на сообщение)\n"
        "/me — твой профиль и статистика\n"
        "/top — вывести топ список\n"
        "/ping — ping-pong\n"
        "/setbcast — (админам, внутри темы) выбрать тему для рассылок\n"
        "/unsetbcast — (админам) сбросить тему рассылок"
    )
    await message.answer(text)
