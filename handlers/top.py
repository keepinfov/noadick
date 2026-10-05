from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import texts
from handlers import cooldowns
from models.disease import check_expire
from presentation import public
from repositories.players import get_chat_lock, get_storage, save_storage
from services import analytics
from services.global_settings import get_config_sync

router = Router()
_bot_username: str | None = None


async def _details_keyboard(bot: Bot, chat_id: int) -> InlineKeyboardMarkup | None:
    global _bot_username
    if _bot_username is None:
        me = await bot.me()
        _bot_username = me.username or ""
    if not _bot_username:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Подробнее в ЛС",
                    url=f"https://t.me/{_bot_username}?start=stats_l_{chat_id}_0",
                )
            ]
        ]
    )


@router.message(Command("top"))
async def cmd_top(message: Message, bot: Bot) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer(texts.GAME_GROUP_ONLY)
        return
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

    data = await analytics.dashboard(
        analytics.Scope("leaderboard", chat_id=chat_id),
        "leaders",
        analytics.DEFAULT_PERIOD,
    )
    png = await analytics.render_png(data)
    await message.answer_photo(
        BufferedInputFile(png, filename="leader-race.png"),
        caption=public.top_caption(data),
        parse_mode="HTML",
        reply_markup=await _details_keyboard(bot, chat_id),
    )
