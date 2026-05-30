import html

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import texts
from handlers.replies import reply_target
from models.disease import DISEASE_BY_ID
from services import cooldown
from services import stats as S

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


def _mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def format_global_profile(stats: S.GlobalProfileStats) -> str:
    name = _mention_from_name(stats.name)
    lines = [texts.global_header(name)]

    if stats.is_banned:
        reason = html.escape(stats.ban_reason) if stats.ban_reason else texts.BAN_NO_REASON
        if stats.ban_until is not None:
            lines.append(texts.global_ban_until(texts.fmt_datetime(stats.ban_until), reason))
        else:
            lines.append(texts.global_ban_forever(reason))

    lines.append("")
    lines.append(texts.GLOBAL_CHATS_HEADER)
    if stats.chats:
        for c in stats.chats:
            lines.append(texts.global_chat_line(html.escape(c.title), c.size, c.rank))
    else:
        lines.append(texts.GLOBAL_NO_CHATS)

    lines.append("")
    lines.append(texts.global_plays(stats.plays, stats.total_grown, stats.total_lost))
    if stats.best_roll is not None and stats.worst_roll is not None:
        lines.append(texts.global_best_worst(stats.best_roll, stats.worst_roll))
    lines.append(
        texts.global_duels(stats.duels_total, stats.wins, stats.losses, stats.winrate)
    )
    lines.append(texts.global_infections(stats.infections))
    lines.append(texts.global_record(stats.best_size_ever))
    if stats.first_play_ts is not None:
        lines.append(texts.global_tenure(texts.fmt_date(stats.first_play_ts)))

    return "\n".join(lines)


def _mention_from_name(name: str) -> str:
    """Global profile is shown in DM/self context, so we render the name as
    plain escaped text rather than a tg:// mention."""
    return f"<b>{html.escape(name)}</b>"


async def _send_global_profile(message: Message, user_id: int, name: str | None) -> None:
    stats = await S.compute_global_profile(user_id, name)
    if not stats.exists:
        await message.answer(texts.GLOBAL_EMPTY)
        return
    await message.answer(format_global_profile(stats), parse_mode="HTML")


@router.message(Command("me"))
async def cmd_me(message: Message, bot: Bot) -> None:
    requester = message.from_user
    if requester is not None and not cooldown.check_and_touch(
        message.chat.id, requester.id, "me", 30
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

    timeline = await S.size_timeline(chat_id, user_id)
    size_spark = S.sparkline([size for _, size in timeline])
    deltas = await S.daily_deltas(chat_id, user_id)
    delta_spark = S.sparkline([d for _, d in deltas])

    name = _mention(user_id, profile.name)
    lines = [
        texts.profile_header(name),
        texts.profile_size(profile.current_size, profile.rank),
        "",
        texts.profile_plays(profile.plays, profile.days_played),
        texts.profile_growth(profile.total_grown, profile.total_lost),
    ]
    if profile.best_day is not None and profile.worst_day is not None:
        lines.append(texts.profile_best_worst(profile.best_day, profile.worst_day))

    lines.append("")
    lines.append(
        texts.profile_duels(profile.duels_total, profile.wins, profile.losses, profile.winrate)
    )
    lines.append(texts.profile_stolen(profile.stolen_total, profile.lost_in_duels))
    lines.append(texts.profile_infections(profile.diseases_caught))

    if profile.current_disease:
        d = DISEASE_BY_ID.get(profile.current_disease)
        if d:
            lines.append(texts.profile_current_disease(d.name))

    if size_spark:
        lines.append("")
        lines.append(texts.profile_size_timeline(size_spark))
    if delta_spark:
        lines.append(texts.profile_deltas(len(deltas), delta_spark))

    # Self-only deep-link to the global profile: the link opens the clicker's
    # own DM and carries no foreign id, so it cannot show someone else's
    # cross-chat stats.
    reply_markup = None
    if message.from_user and target.id == message.from_user.id:
        link = await _global_link(bot)
        if link:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=texts.GLOBAL_BUTTON, url=link)]
                ]
            )

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=reply_markup)
