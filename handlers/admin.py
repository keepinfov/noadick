"""In-Telegram global admin panel.

The UI is a thin layer over services.admin_actions (which are Telegram-agnostic
and reusable by a future web panel). Access is restricted to user ids listed in
the ADMIN_IDS environment variable (comma-separated).
"""
from __future__ import annotations

import os

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from models.disease import DISEASES, disease_tag
from repositories import chats as chats_repo
from repositories import players as players_repo
from services import admin_actions

router = Router()

CHATS_PER_PAGE = 8
PLAYERS_PER_CHAT = 15


def admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_IDS", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


class IsGlobalAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user and user.id in admin_ids())


router.message.filter(IsGlobalAdmin())
router.callback_query.filter(IsGlobalAdmin())


class AdminStates(StatesGroup):
    set_size = State()
    set_name = State()
    find_query = State()
    broadcast_text = State()


# ---------------------------------------------------------------- rendering ---


def _player_line(p) -> str:
    tag = disease_tag(_player_dict(p))
    return f"{p.name}{tag} — {p.size} см (id {p.user_id})"


def _player_dict(p) -> dict:
    d = {"name": p.name, "size": p.size, "last": p.last_play}
    if p.disease_id:
        d["disease"] = {"id": p.disease_id, "caught_at": p.disease_caught_at}
    return d


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Чаты", callback_data="adm:chats:0"),
                InlineKeyboardButton(text="🔎 Поиск игрока", callback_data="adm:find"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
                InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:bcast"),
            ],
        ]
    )


async def render_chats(page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = await chats_repo.count_chats()
    offset = page * CHATS_PER_PAGE
    chats = await chats_repo.list_chats(offset=offset, limit=CHATS_PER_PAGE)

    rows: list[list[InlineKeyboardButton]] = []
    for c in chats:
        title = c.title or (f"private {c.chat_id}" if c.type == "private" else str(c.chat_id))
        flag = "🚫 " if c.is_banned else ""
        rows.append(
            [InlineKeyboardButton(text=f"{flag}{title}", callback_data=f"adm:chat:{c.chat_id}")]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="« Назад", callback_data=f"adm:chats:{page - 1}"))
    if offset + CHATS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Вперёд »", callback_data=f"adm:chats:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="adm:home")])

    text = f"💬 Чатов всего: {total}. Страница {page + 1}."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_chat(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await chats_repo.get_chat(chat_id)
    stats = await players_repo.chat_player_stats(chat_id)
    players = await players_repo.top_players(chat_id, PLAYERS_PER_CHAT)

    title = (chat.title if chat and chat.title else str(chat_id))
    banned = chat and chat.is_banned
    lines = [
        f"💬 <b>{title}</b> (id {chat_id})",
        f"Игроков: {stats['players']} | сумма: {stats['total_size']} | макс: {stats['biggest']}",
        "",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for p in players:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_player_line(p),
                    callback_data=f"adm:p:{chat_id}:{p.user_id}",
                )
            ]
        )
    if not players:
        lines.append("Нет игроков.")

    rows.append(
        [
            InlineKeyboardButton(text="🧨 Сброс чата", callback_data=f"adm:rchat:{chat_id}"),
            InlineKeyboardButton(
                text="✅ Разбан" if banned else "🚫 Бан чата",
                callback_data=f"adm:{'uchat' if banned else 'bchat'}:{chat_id}",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text="« К списку", callback_data="adm:chats:0")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def render_player(chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    p = await players_repo.get_player(chat_id, user_id)
    if p is None:
        return "Игрок не найден.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="« Назад", callback_data=f"adm:chat:{chat_id}")]]
        )
    user = await chats_repo.get_user(user_id)
    username = f"@{user.username}" if user and user.username else "—"
    tag = disease_tag(_player_dict(p))
    text = (
        f"👤 <b>{p.name}</b>{tag}\n"
        f"id: {user_id} | {username}\n"
        f"Размер: <b>{p.size}</b> см\n"
        f"Чат: {chat_id}"
    )
    base = f"{chat_id}:{user_id}"
    rows = [
        [
            InlineKeyboardButton(text="-10", callback_data=f"adm:add:{base}:-10"),
            InlineKeyboardButton(text="-1", callback_data=f"adm:add:{base}:-1"),
            InlineKeyboardButton(text="+1", callback_data=f"adm:add:{base}:1"),
            InlineKeyboardButton(text="+10", callback_data=f"adm:add:{base}:10"),
        ],
        [
            InlineKeyboardButton(text="🔢 Задать размер", callback_data=f"adm:setsz:{base}"),
            InlineKeyboardButton(text="✏️ Имя", callback_data=f"adm:setname:{base}"),
        ],
        [
            InlineKeyboardButton(text="🦠 Болезнь", callback_data=f"adm:disl:{base}"),
            InlineKeyboardButton(text="💊 Вылечить", callback_data=f"adm:cure:{base}"),
        ],
        [
            InlineKeyboardButton(text="♻️ Сброс", callback_data=f"adm:rp:{base}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"adm:del:{base}"),
        ],
        [
            InlineKeyboardButton(text="🚫 Бан юзера", callback_data=f"adm:buser:{user_id}"),
            InlineKeyboardButton(text="« К чату", callback_data=f"adm:chat:{chat_id}"),
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def disease_kb(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    base = f"{chat_id}:{user_id}"
    rows = [
        [InlineKeyboardButton(text=d.name, callback_data=f"adm:dis:{base}:{d.id}")]
        for d in DISEASES
    ]
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"adm:p:{base}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None) -> None:
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# ----------------------------------------------------------------- handlers ---


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🛠 Админ-панель", reply_markup=main_menu_kb())


@router.callback_query(F.data == "adm:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, "🛠 Админ-панель", main_menu_kb())


@router.callback_query(F.data.startswith("adm:chats:"))
async def cb_chats(callback: CallbackQuery) -> None:
    page = int(callback.data.split(":")[2])
    text, kb = await render_chats(page)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:chat:"))
async def cb_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:p:"))
async def cb_player(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:add:"))
async def cb_add(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id, delta = callback.data.split(":")
    await admin_actions.add_size(callback.from_user.id, int(chat_id), int(user_id), int(delta))
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:cure:"))
async def cb_cure(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await admin_actions.cure(callback.from_user.id, int(chat_id), int(user_id))
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:rp:"))
async def cb_reset_player(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await admin_actions.reset_player(callback.from_user.id, int(chat_id), int(user_id))
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_delete_player(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await admin_actions.delete_player(callback.from_user.id, int(chat_id), int(user_id))
    text, kb = await render_chat(int(chat_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:buser:"))
async def cb_ban_user(callback: CallbackQuery) -> None:
    user_id = int(callback.data.split(":")[2])
    res = await admin_actions.ban_user(callback.from_user.id, user_id)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:rchat:"))
async def cb_reset_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    res = await admin_actions.reset_chat(callback.from_user.id, chat_id)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:bchat:"))
async def cb_ban_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    await admin_actions.ban_chat(callback.from_user.id, chat_id)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:uchat:"))
async def cb_unban_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    await admin_actions.unban_chat(callback.from_user.id, chat_id)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:disl:"))
async def cb_disease_list(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await _edit(callback, "Выбери болезнь:", disease_kb(int(chat_id), int(user_id)))


@router.callback_query(F.data.startswith("adm:dis:"))
async def cb_disease_set(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id, disease_id = callback.data.split(":")
    await admin_actions.give_disease(callback.from_user.id, int(chat_id), int(user_id), disease_id)
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery) -> None:
    s = await chats_repo.global_stats()
    text = (
        "📊 <b>Глобальная статистика</b>\n"
        f"Чатов: {s['chats']}\n"
        f"Пользователей: {s['users']}\n"
        f"Игроков (записей): {s['players']}\n"
        f"Суммарный размер: {s['total_size']} см"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Меню", callback_data="adm:home")]]
    )
    await _edit(callback, text, kb)


# ---- FSM flows: set size, set name, find, broadcast ----


@router.callback_query(F.data.startswith("adm:setsz:"))
async def cb_set_size(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await state.set_state(AdminStates.set_size)
    await state.update_data(chat_id=int(chat_id), user_id=int(user_id))
    await _edit(callback, "Введи новый размер (целое число):", None)


@router.message(AdminStates.set_size)
async def msg_set_size(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if not message.text or not message.text.strip().lstrip("-").isdigit():
        await message.answer("Нужно целое число. Отменено.", reply_markup=main_menu_kb())
        return
    res = await admin_actions.set_size(
        message.from_user.id, data["chat_id"], data["user_id"], int(message.text.strip())
    )
    text, kb = await render_player(data["chat_id"], data["user_id"])
    await message.answer(res.message)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:setname:"))
async def cb_set_name(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await state.set_state(AdminStates.set_name)
    await state.update_data(chat_id=int(chat_id), user_id=int(user_id))
    await _edit(callback, "Введи новое имя:", None)


@router.message(AdminStates.set_name)
async def msg_set_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пустое имя. Отменено.", reply_markup=main_menu_kb())
        return
    await admin_actions.set_name(message.from_user.id, data["chat_id"], data["user_id"], name)
    text, kb = await render_player(data["chat_id"], data["user_id"])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm:find")
async def cb_find(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.find_query)
    await _edit(callback, "Введи ID игрока или часть имени:", None)


@router.message(AdminStates.find_query)
async def msg_find(message: Message, state: FSMContext) -> None:
    await state.clear()
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пустой запрос. Отменено.", reply_markup=main_menu_kb())
        return
    results = await players_repo.find_players(query)
    if not results:
        await message.answer("Ничего не найдено.", reply_markup=main_menu_kb())
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"{p.name} — {p.size} см (чат {p.chat_id})",
                callback_data=f"adm:p:{p.chat_id}:{p.user_id}",
            )
        ]
        for p in results
    ]
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="adm:home")])
    await message.answer(
        f"Найдено: {len(results)}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast_text)
    await _edit(callback, "Введи текст рассылки (HTML). Будет отправлено во все чаты:", None)


@router.message(AdminStates.broadcast_text)
async def msg_bcast(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    text = message.html_text if message.text else None
    if not text:
        await message.answer("Пустой текст. Отменено.", reply_markup=main_menu_kb())
        return
    targets = await admin_actions.broadcast_targets()
    sent = 0
    failed = 0
    for chat_id in targets:
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"📢 Рассылка завершена. Успешно: {sent}, ошибок: {failed}.",
        reply_markup=main_menu_kb(),
    )
