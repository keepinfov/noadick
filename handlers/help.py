from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import texts
from handlers.profile import _send_global_profile

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject) -> None:
    if command.args == "me" and message.from_user:
        await _send_global_profile(message, message.from_user.id, message.from_user.first_name)
        return
    await message.answer(texts.START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)
