from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import texts
from handlers import cooldowns
from models.disease import check_expire, disease_tag
from repositories.players import get_chat_lock, get_storage, save_storage
from services.global_settings import get_config_sync
from services.wealth import chat_rows

router = Router()


async def _race_keyboard(bot: Bot, chat_id: int) -> InlineKeyboardMarkup | None:
    me = await bot.me()
    if not me.username:
        return None
    url = f"https://t.me/{me.username}?start=stats_l_{chat_id}_0"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏁 Гонка лидеров", url=url)]]
    )


@router.message(Command("top"))
async def cmd_top(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id

    user = message.from_user
    if user is not None and not await cooldowns.passes(
        message, user.id, "top", get_config_sync().cd_top
    ):
        return

    async with get_chat_lock(chat_id):
        storage = await get_storage(chat_id)

        changed = False
        for pid in list(storage.keys()):
            if check_expire(storage[pid]):
                changed = True
        if changed:
            await save_storage(chat_id, storage)

    if not storage:
        await message.answer(texts.TOP_EMPTY)
        return

    top10 = (await chat_rows(chat_id))[:10]

    lines = [texts.TOP_HEADER]
    for i, player in enumerate(top10):
        tag = disease_tag(storage.get(str(player.user_id), {}))
        lines.append(texts.top_line(i + 1, player.name, tag, player.net))

    keyboard = (
        await _race_keyboard(bot, chat_id) if message.chat.type in {"group", "supergroup"} else None
    )
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
