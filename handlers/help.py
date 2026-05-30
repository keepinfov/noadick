from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import texts
from handlers import cooldowns
from handlers.profile import _send_global_profile
from services.admins import is_global_admin
from services.global_settings import get_config_sync

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject) -> None:
    if command.args == "me" and message.from_user:
        await _send_global_profile(message, message.from_user.id, message.from_user.first_name)
        return
    await message.answer(texts.START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    user = message.from_user
    if user is not None and not await cooldowns.passes(
        message, user.id, "help", get_config_sync().cd_help
    ):
        return
    text = texts.HELP
    if user and is_global_admin(user.id):
        text += texts.HELP_ADMIN
    await message.answer(text)
