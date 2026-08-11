from __future__ import annotations

import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from callbacks import StatsCallback
from handlers.replies import reply_target
from repositories import chats as chats_repo
from services import analytics
from services.admins import is_global_admin
from services.chat_admin import is_chat_admin

router = Router()

_SECTION_LABELS = {
    "overview": "📌 Обзор",
    "growth": "🍆 Рост",
    "duels": "⚔️ Дуэли",
    "bank": "🏦 Банк",
    "poker": "🃏 Покер",
    "health": "🩹 PISYAGO",
    "leaders": "🏆 Лидеры",
    "corp": "🏢 Корпорация",
}


def _scope(code: str, chat_id: int, user_id: int) -> analytics.Scope:
    return analytics.Scope(
        {"u": "user", "p": "personal", "c": "chat", "l": "leaderboard", "g": "global"}[code],
        chat_id,
        user_id,
    )


async def _allowed(bot: Bot, viewer: int, scope: analytics.Scope) -> bool:
    if is_global_admin(viewer):
        return True
    if scope.kind == "personal":
        return viewer == scope.user_id
    if scope.kind == "user":
        return viewer == scope.user_id or await is_chat_admin(bot, scope.chat_id, viewer)
    if scope.kind == "leaderboard":
        chat = await chats_repo.get_chat(scope.chat_id)
        return bool(chat and chat.type in {"group", "supergroup"})
    if scope.kind == "chat":
        return await is_chat_admin(bot, scope.chat_id, viewer)
    return False


def _section_allowed(scope: analytics.Scope, section: str) -> bool:
    if scope.kind == "leaderboard":
        return section == "leaders"
    return section not in {"leaders", "corp"} or scope.kind in {"chat", "global"}


def _kb(code: str, scope: analytics.Scope, section: str, period: str) -> InlineKeyboardMarkup:
    sections = (
        ["leaders"]
        if scope.kind == "leaderboard"
        else ["overview", "growth", "duels", "bank", "poker", "health"]
    )
    if scope.kind in {"chat", "global"}:
        sections.extend(["leaders", "corp"])
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(sections), 2):
        row = []
        for item in sections[index : index + 2]:
            label = _SECTION_LABELS[item]
            if item == section:
                label = f"• {label}"
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=StatsCallback(
                        action="show",
                        scope=code,
                        section=item,
                        period=period,
                        chat_id=scope.chat_id,
                        user_id=scope.user_id,
                    ).pack(),
                )
            )
        rows.append(row)
    period_row = []
    for key, label in (("d", "Сегодня"), ("7", "7д"), ("30", "30д"), ("a", "Всё")):
        period_row.append(
            InlineKeyboardButton(
                text=f"• {label}" if key == period else label,
                callback_data=StatsCallback(
                    action="show",
                    scope=code,
                    section=section,
                    period=key,
                    chat_id=scope.chat_id,
                    user_id=scope.user_id,
                ).pack(),
            )
        )
    rows.append(period_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="📄 CSV",
                callback_data=StatsCallback(
                    action="csv",
                    scope=code,
                    section=section,
                    period=period,
                    chat_id=scope.chat_id,
                    user_id=scope.user_id,
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_panel(
    message: Message, scope: analytics.Scope, code: str, section: str = "overview"
) -> None:
    period = analytics.DEFAULT_PERIOD
    data = await analytics.dashboard(scope, section, period)
    png = await analytics.render_png(data)
    await message.answer_photo(
        BufferedInputFile(png, filename="stats.png"),
        caption=analytics.caption(data),
        reply_markup=_kb(code, scope, section, period),
        parse_mode="HTML",
    )


async def _bot_link(bot: Bot, payload: str) -> str | None:
    me = await bot.me()
    return f"https://t.me/{me.username}?start={payload}" if me.username else None


@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if message.chat.type == "private":
        if (command.args or "").strip().lower() == "global" and is_global_admin(user.id):
            await send_panel(message, analytics.Scope("global"), "g")
        else:
            await send_panel(message, analytics.Scope("personal", user_id=user.id), "p")
        return

    target = reply_target(message)
    wants_chat = (command.args or "").strip().lower() in {"chat", "чат"}
    admin = is_global_admin(user.id) or await is_chat_admin(bot, message.chat.id, user.id)
    if wants_chat:
        code = "c" if admin else "l"
        payload = f"stats_{code}_{message.chat.id}_0"
        label = "📊 Статистика чата в ЛС" if admin else "🏁 Гонка лидеров в ЛС"
    else:
        target_id = target.id if target else user.id
        if target_id != user.id and not admin:
            await message.answer("Чужую подробную статистику тебе не покажут. Дрочи свой отчёт.")
            return
        payload = f"stats_u_{message.chat.id}_{target_id}"
        label = "📊 Подробная статистика в ЛС"
    link = await _bot_link(bot, payload)
    if not link:
        await message.answer(
            "У бота нет username, так что Telegram опять блеснул инженерной мыслью."
        )
        return
    await message.answer(
        "Подробности вынесены в личку: чужие долги в общий чат не вываливаем.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, url=link)]]
        ),
    )


@router.message(Command("start"), F.chat.type == "private")
async def start_stats(message: Message, command: CommandObject, bot: Bot) -> None:
    args = command.args or ""
    if not args.startswith("stats_") or message.from_user is None:
        return
    try:
        _, code, chat_raw, user_raw = args.split("_", 3)
        scope = _scope(code, int(chat_raw), int(user_raw))
    except (KeyError, ValueError):
        await message.answer("Ссылка на статистику протухла или её собирал пьяный калькулятор.")
        return
    if not await _allowed(bot, message.from_user.id, scope):
        await message.answer("Руки убрал: на эту статистику у тебя прав не выросло.")
        return
    await send_panel(message, scope, code, "leaders" if code == "l" else "overview")


@router.callback_query(StatsCallback.filter())
async def stats_callback(callback: CallbackQuery, callback_data: StatsCallback, bot: Bot) -> None:
    try:
        scope = _scope(callback_data.scope, callback_data.chat_id, callback_data.user_id)
    except KeyError:
        await callback.answer("Кнопка сгнила. Открой /stats заново.", show_alert=True)
        return
    if not await _allowed(bot, callback.from_user.id, scope):
        await callback.answer("Чужие цифры тебе не дадут, любопытная задница.", show_alert=True)
        return
    if not _section_allowed(scope, callback_data.section):
        await callback.answer("Кнопку подделал, а мозг к ней не приложил.", show_alert=True)
        return
    try:
        data = await analytics.dashboard(scope, callback_data.section, callback_data.period)
    except ValueError:
        await callback.answer("Эта кнопка уже сгнила. Открой /stats заново.", show_alert=True)
        return
    if callback_data.action == "csv":
        filename = f"noadick-{callback_data.section}-{callback_data.period}.csv"
        await bot.send_document(
            callback.from_user.id,
            BufferedInputFile(analytics.render_csv(data), filename=filename),
            caption=f"CSV: {html.escape(data.title)} · {analytics.PERIODS[data.period]}",
        )
        await callback.answer("Выгрузка уже в личке.")
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    png = await analytics.render_png(data)
    try:
        await callback.message.edit_media(
            InputMediaPhoto(
                media=BufferedInputFile(png, filename="stats.png"),
                caption=analytics.caption(data),
                parse_mode="HTML",
            ),
            reply_markup=_kb(
                callback_data.scope, scope, callback_data.section, callback_data.period
            ),
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    await callback.answer()
