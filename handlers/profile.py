import html

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import texts
from handlers import cooldowns
from handlers.replies import reply_target
from models.disease import DISEASE_BY_ID
from presentation import public
from repositories import poker as poker_repo
from services import bank
from services import stats as S
from services.global_settings import get_config_sync

router = Router()

_bot_username: str | None = None


async def _global_link(bot: Bot) -> str | None:
    global _bot_username
    if _bot_username is None:
        me = await bot.me()
        _bot_username = me.username or ""
    if not _bot_username:
        return None
    return f"https://t.me/{_bot_username}?start=me"


async def _stats_link(bot: Bot, chat_id: int, user_id: int) -> str | None:
    if await _global_link(bot) is None or not _bot_username:
        return None
    return f"https://t.me/{_bot_username}?start=stats_u_{chat_id}_{user_id}"


def _mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def format_global_profile(stats: S.GlobalProfileStats) -> str:
    return public.global_profile(stats)


async def _send_global_profile(message: Message, user_id: int, name: str | None) -> None:
    stats = await S.compute_global_profile(user_id, name)
    if not stats.exists:
        await message.answer(texts.GLOBAL_EMPTY)
        return
    await message.answer(format_global_profile(stats), parse_mode="HTML")


@router.message(Command("me"))
async def cmd_me(message: Message, bot: Bot) -> None:
    requester = message.from_user
    if requester is not None and not await cooldowns.passes(
        message, requester.id, "me", get_config_sync().cd_me
    ):
        return

    if message.chat.type == "private":
        user = message.from_user
        if not user:
            return
        await _send_global_profile(message, user.id, user.first_name)
        return

    chat_id = message.chat.id
    target = reply_target(message) or message.from_user
    if not target:
        return

    user_id = target.id
    profile = await S.compute_profile(chat_id, user_id)

    if not profile.exists:
        await message.answer(
            texts.profile_not_played(_mention(user_id, target.first_name)),
            parse_mode="HTML",
        )
        return

    own_profile = bool(message.from_user and target.id == message.from_user.id)
    bank_line = None
    poker_stack = 0
    if own_profile:
        bank_line = texts.profile_bank(await bank.get_summary(chat_id, user_id))
        poker_stack = await poker_repo.get_money_stack(chat_id, user_id)
    disease_name = ""
    if profile.current_disease:
        d = DISEASE_BY_ID.get(profile.current_disease)
        if d:
            disease_name = d.name

    # Self-only deep-link to the global profile: the link opens the clicker's
    # own DM and carries no foreign id, so it cannot show someone else's
    # cross-chat stats.
    reply_markup = None
    if own_profile:
        global_link = await _global_link(bot)
        stats_link = await _stats_link(bot, chat_id, user_id)
        buttons = []
        if stats_link:
            buttons.append(InlineKeyboardButton(text="📊 Подробная статистика", url=stats_link))
        if global_link:
            buttons.append(InlineKeyboardButton(text=texts.GLOBAL_BUTTON, url=global_link))
        if buttons:
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[[button] for button in buttons])

    await message.answer(
        public.profile(
            profile,
            user_id=user_id,
            bank_line=bank_line,
            poker_stack=poker_stack,
            disease=disease_name,
        ),
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
