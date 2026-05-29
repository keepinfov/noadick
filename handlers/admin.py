"""In-Telegram global admin panel.

The UI is a thin layer over services.admin_actions (which are Telegram-agnostic
and reusable by a future web panel). Access is restricted to user ids listed in
the ADMIN_IDS environment variable (comma-separated).
"""
from __future__ import annotations

import time

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

import texts
from models.disease import DISEASES, disease_tag
from repositories import chats as chats_repo
from repositories import players as players_repo
from repositories import threads as threads_repo
from services import admin_actions
from services.admins import admin_ids

router = Router()

CHATS_PER_PAGE = 8
PLAYERS_PER_CHAT = 15


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
    broadcast_confirm = State()
    ban_reason = State()
    ban_duration = State()


BAN_REASONS = texts.BAN_REASONS
BAN_REASON_TEXT = texts.BAN_REASON_TEXT


# ---------------------------------------------------------------- rendering ---


def _player_line(p) -> str:
    tag = disease_tag(_player_dict(p))
    return texts.admin_player_line(p.name, tag, p.size, p.user_id)


def _player_dict(p) -> dict:
    d = {"name": p.name, "size": p.size, "last": p.last_play}
    if p.disease_id:
        d["disease"] = {"id": p.disease_id, "caught_at": p.disease_caught_at}
    return d


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.BTN_CHATS, callback_data="adm:chats:0"),
                InlineKeyboardButton(text=texts.BTN_FIND, callback_data="adm:find"),
            ],
            [
                InlineKeyboardButton(text=texts.BTN_STATS, callback_data="adm:stats"),
                InlineKeyboardButton(text=texts.BTN_BCAST, callback_data="adm:bcast"),
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
        nav.append(InlineKeyboardButton(text=texts.BTN_PREV, callback_data=f"adm:chats:{page - 1}"))
    if offset + CHATS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text=texts.BTN_NEXT, callback_data=f"adm:chats:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])

    text = texts.admin_chats_page(total, page)
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_chat(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    chat = await chats_repo.get_chat(chat_id)
    stats = await players_repo.chat_player_stats(chat_id)
    players = await players_repo.top_players(chat_id, PLAYERS_PER_CHAT)

    title = (chat.title if chat and chat.title else str(chat_id))
    banned = chat and chat.is_banned
    lines = [
        texts.admin_chat_header(title, chat_id),
        texts.admin_chat_stats(stats['players'], stats['total_size'], stats['biggest']),
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
        lines.append(texts.ADMIN_NO_PLAYERS)

    rows.append(
        [
            InlineKeyboardButton(text=texts.BTN_RESET_CHAT, callback_data=f"adm:rchat:{chat_id}"),
            InlineKeyboardButton(
                text=texts.BTN_UNBAN if banned else texts.BTN_BAN_CHAT,
                callback_data=f"adm:{'uchat' if banned else 'bchat'}:{chat_id}",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK_LIST, callback_data="adm:chats:0")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def render_player(chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    p = await players_repo.get_player(chat_id, user_id)
    if p is None:
        return texts.ADMIN_PLAYER_NOT_FOUND, InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_BACK, callback_data=f"adm:chat:{chat_id}")]]
        )
    user = await chats_repo.get_user(user_id)
    username = f"@{user.username}" if user and user.username else "—"
    tag = disease_tag(_player_dict(p))
    text = texts.admin_player_header(p.name, tag, user_id, username, p.size, chat_id)
    base = f"{chat_id}:{user_id}"
    rows = [
        [
            InlineKeyboardButton(text="-10", callback_data=f"adm:add:{base}:-10"),
            InlineKeyboardButton(text="-1", callback_data=f"adm:add:{base}:-1"),
            InlineKeyboardButton(text="+1", callback_data=f"adm:add:{base}:1"),
            InlineKeyboardButton(text="+10", callback_data=f"adm:add:{base}:10"),
        ],
        [
            InlineKeyboardButton(text=texts.BTN_SET_SIZE, callback_data=f"adm:setsz:{base}"),
            InlineKeyboardButton(text=texts.BTN_SET_NAME, callback_data=f"adm:setname:{base}"),
        ],
        [
            InlineKeyboardButton(text=texts.BTN_GIVE_DISEASE, callback_data=f"adm:disl:{base}"),
            InlineKeyboardButton(text=texts.BTN_CURE, callback_data=f"adm:cure:{base}"),
        ],
        [
            InlineKeyboardButton(text=texts.BTN_RESET_PLAYER, callback_data=f"adm:rp:{base}"),
            InlineKeyboardButton(text=texts.BTN_DELETE_PLAYER, callback_data=f"adm:del:{base}"),
        ],
        [
            InlineKeyboardButton(text=texts.BTN_BAN_USER, callback_data=f"adm:buser:{chat_id}:{user_id}"),
            InlineKeyboardButton(text=texts.BTN_BACK_CHAT, callback_data=f"adm:chat:{chat_id}"),
        ],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def disease_kb(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    base = f"{chat_id}:{user_id}"
    rows = [
        [InlineKeyboardButton(text=d.name, callback_data=f"adm:dis:{base}:{d.id}")]
        for d in DISEASES
    ]
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK, callback_data=f"adm:p:{base}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None) -> None:
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


def _confirm_kb(yes_data: str, back_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.BTN_YES, callback_data=yes_data),
                InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=back_data),
            ]
        ]
    )


def _ban_user_reason_kb(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=txt, callback_data=f"adm:bur:{chat_id}:{user_id}:{rid}"
            )
        ]
        for rid, txt in BAN_REASONS
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_OWN_REASON, callback_data=f"adm:burc:{chat_id}:{user_id}"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=f"adm:p:{chat_id}:{user_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ban_duration_kb(prefix: str, cancel_data: str) -> InlineKeyboardMarkup:
    """Duration picker. `prefix` already carries chat/user/reason context;
    each button appends the duration id."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:{d_id}")]
        for d_id, label, _ in texts.BAN_DURATIONS
    ]
    rows.append([InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=cancel_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ban_until_from(dur_id: str) -> int | None:
    secs = texts.BAN_DURATION_SECS.get(dur_id)
    return int(time.time()) + secs if secs is not None else None


def _ban_chat_reason_kb(chat_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=txt, callback_data=f"adm:bcr:{chat_id}:{rid}")]
        for rid, txt in BAN_REASONS
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_OWN_REASON, callback_data=f"adm:bcrc:{chat_id}"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=f"adm:chat:{chat_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _notify_user_banned(
    bot: Bot, user_id: int, reason: str | None, ban_until: int | None
) -> None:
    """Best-effort DM to a banned user. Fails silently if they never DMed."""
    suffix = texts.ban_reason_suffix(reason)
    if ban_until is not None:
        suffix += texts.ban_until_suffix(texts.fmt_datetime(ban_until))
    try:
        await bot.send_message(user_id, texts.notify_user_banned(suffix))
    except Exception:
        pass


async def _notify_chat_banned(bot: Bot, chat_id: int, reason: str | None) -> None:
    """Best-effort in-chat notice that the chat was banned."""
    suffix = texts.ban_reason_suffix(reason)
    try:
        await bot.send_message(chat_id, texts.notify_chat_banned(suffix))
    except Exception:
        pass


# ----------------------------------------------------------------- handlers ---


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.ADMIN_TITLE, reply_markup=main_menu_kb())


@router.callback_query(F.data == "adm:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, texts.ADMIN_TITLE, main_menu_kb())


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
    p = await players_repo.get_player(int(chat_id), int(user_id))
    name = p.name if p else user_id
    await _edit(
        callback,
        texts.admin_confirm_reset_player(name),
        _confirm_kb(f"adm:yes:rp:{chat_id}:{user_id}", f"adm:p:{chat_id}:{user_id}"),
    )


@router.callback_query(F.data.startswith("adm:yes:rp:"))
async def cb_do_reset_player(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    chat_id, user_id = int(parts[3]), int(parts[4])
    await admin_actions.reset_player(callback.from_user.id, chat_id, user_id)
    text, kb = await render_player(chat_id, user_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_delete_player(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    p = await players_repo.get_player(int(chat_id), int(user_id))
    name = p.name if p else user_id
    await _edit(
        callback,
        texts.admin_confirm_delete_player(name),
        _confirm_kb(f"adm:yes:del:{chat_id}:{user_id}", f"adm:p:{chat_id}:{user_id}"),
    )


@router.callback_query(F.data.startswith("adm:yes:del:"))
async def cb_do_delete_player(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    chat_id, user_id = int(parts[3]), int(parts[4])
    await admin_actions.delete_player(callback.from_user.id, chat_id, user_id)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:buser:"))
async def cb_ban_user(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    u = await chats_repo.get_user(int(user_id))
    name = u.first_name if u and u.first_name else user_id
    await _edit(
        callback,
        texts.admin_ask_ban_user_reason(name, user_id),
        _ban_user_reason_kb(int(chat_id), int(user_id)),
    )


@router.callback_query(F.data.startswith("adm:bur:"))
async def cb_ban_user_reason(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    chat_id, user_id, reason_id = int(parts[3]), int(parts[4]), parts[5]
    prefix = f"adm:burx:{chat_id}:{user_id}:{reason_id}"
    await _edit(
        callback,
        texts.ADMIN_PICK_BAN_DURATION,
        _ban_duration_kb(prefix, f"adm:p:{chat_id}:{user_id}"),
    )


@router.callback_query(F.data.startswith("adm:burx:"))
async def cb_ban_user_apply(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    chat_id, user_id, reason_id, dur_id = (
        int(parts[2]), int(parts[3]), parts[4], parts[5]
    )
    reason = BAN_REASON_TEXT.get(reason_id)
    ban_until = _ban_until_from(dur_id)
    res = await admin_actions.ban_user(
        callback.from_user.id, user_id, reason=reason, ban_until=ban_until
    )
    if res.ok:
        await _notify_user_banned(bot, user_id, reason, ban_until)
    text, kb = await render_player(chat_id, user_id)
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:burc:"))
async def cb_ban_user_custom(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    chat_id, user_id = int(parts[2]), int(parts[3])
    await state.set_state(AdminStates.ban_reason)
    await state.update_data(ban_target="user", chat_id=chat_id, user_id=user_id)
    await _edit(callback, texts.ADMIN_ENTER_BAN_REASON, None)


@router.callback_query(F.data.startswith("adm:burxc:"))
async def cb_ban_user_apply_custom(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    await state.clear()
    parts = callback.data.split(":")
    chat_id, user_id, dur_id = int(parts[2]), int(parts[3]), parts[4]
    reason = data.get("ban_reason_text")
    ban_until = _ban_until_from(dur_id)
    res = await admin_actions.ban_user(
        callback.from_user.id, user_id, reason=reason, ban_until=ban_until
    )
    if res.ok:
        await _notify_user_banned(bot, user_id, reason, ban_until)
    text, kb = await render_player(chat_id, user_id)
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:rchat:"))
async def cb_reset_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    chat = await chats_repo.get_chat(chat_id)
    title = chat.title if chat and chat.title else str(chat_id)
    await _edit(
        callback,
        texts.admin_confirm_reset_chat(title),
        _confirm_kb(f"adm:yes:rchat:{chat_id}", f"adm:chat:{chat_id}"),
    )


@router.callback_query(F.data.startswith("adm:yes:rchat:"))
async def cb_do_reset_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[3])
    res = await admin_actions.reset_chat(callback.from_user.id, chat_id)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:bchat:"))
async def cb_ban_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    chat = await chats_repo.get_chat(chat_id)
    title = chat.title if chat and chat.title else str(chat_id)
    await _edit(
        callback,
        texts.admin_ask_ban_chat_reason(title),
        _ban_chat_reason_kb(chat_id),
    )


@router.callback_query(F.data.startswith("adm:bcr:"))
async def cb_ban_chat_reason(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    chat_id, reason_id = int(parts[3]), parts[4]
    reason = BAN_REASON_TEXT.get(reason_id)
    res = await admin_actions.ban_chat(callback.from_user.id, chat_id, reason=reason)
    if res.ok:
        await _notify_chat_banned(bot, chat_id, reason)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:bcrc:"))
async def cb_ban_chat_custom(callback: CallbackQuery, state: FSMContext) -> None:
    chat_id = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.ban_reason)
    await state.update_data(ban_target="chat", chat_id=chat_id)
    await _edit(callback, texts.ADMIN_ENTER_BAN_CHAT_REASON, None)


@router.message(AdminStates.ban_reason)
async def msg_ban_reason(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    reason = (message.text or "").strip()
    if not reason:
        await state.clear()
        await message.answer(texts.ADMIN_REASON_EMPTY, reply_markup=main_menu_kb())
        return
    if data.get("ban_target") == "chat":
        await state.clear()
        chat_id = data["chat_id"]
        res = await admin_actions.ban_chat(message.from_user.id, chat_id, reason=reason)
        if res.ok:
            await _notify_chat_banned(bot, chat_id, reason)
        text, kb = await render_chat(chat_id)
        await message.answer(res.message)
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        chat_id, user_id = data["chat_id"], data["user_id"]
        await state.update_data(ban_reason_text=reason)
        await state.set_state(AdminStates.ban_duration)
        prefix = f"adm:burxc:{chat_id}:{user_id}"
        await message.answer(
            texts.ADMIN_PICK_BAN_DURATION,
            reply_markup=_ban_duration_kb(prefix, f"adm:p:{chat_id}:{user_id}"),
        )


@router.callback_query(F.data.startswith("adm:uchat:"))
async def cb_unban_chat(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    await admin_actions.unban_chat(callback.from_user.id, chat_id)
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:disl:"))
async def cb_disease_list(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await _edit(callback, texts.ADMIN_PICK_DISEASE, disease_kb(int(chat_id), int(user_id)))


@router.callback_query(F.data.startswith("adm:dis:"))
async def cb_disease_set(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id, disease_id = callback.data.split(":")
    await admin_actions.give_disease(callback.from_user.id, int(chat_id), int(user_id), disease_id)
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery) -> None:
    s = await chats_repo.global_stats()
    text = texts.admin_global_stats(s['chats'], s['users'], s['players'], s['total_size'])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")]]
    )
    await _edit(callback, text, kb)


# ---- FSM flows: set size, set name, find, broadcast ----


@router.callback_query(F.data.startswith("adm:setsz:"))
async def cb_set_size(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    await state.set_state(AdminStates.set_size)
    await state.update_data(chat_id=int(chat_id), user_id=int(user_id))
    await _edit(callback, texts.ADMIN_ENTER_SIZE, None)


@router.message(AdminStates.set_size)
async def msg_set_size(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if not message.text or not message.text.strip().lstrip("-").isdigit():
        await message.answer(texts.ADMIN_SIZE_NOT_INT, reply_markup=main_menu_kb())
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
    await _edit(callback, texts.ADMIN_ENTER_NAME, None)


@router.message(AdminStates.set_name)
async def msg_set_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    name = (message.text or "").strip()
    if not name:
        await message.answer(texts.ADMIN_NAME_EMPTY, reply_markup=main_menu_kb())
        return
    await admin_actions.set_name(message.from_user.id, data["chat_id"], data["user_id"], name)
    text, kb = await render_player(data["chat_id"], data["user_id"])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm:find")
async def cb_find(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.find_query)
    await _edit(callback, texts.ADMIN_ENTER_FIND, None)


@router.message(AdminStates.find_query)
async def msg_find(message: Message, state: FSMContext) -> None:
    await state.clear()
    query = (message.text or "").strip()
    if not query:
        await message.answer(texts.ADMIN_FIND_EMPTY, reply_markup=main_menu_kb())
        return
    results = await players_repo.find_players(query)
    if not results:
        await message.answer(texts.ADMIN_FIND_NONE, reply_markup=main_menu_kb())
        return
    rows = [
        [
            InlineKeyboardButton(
                text=texts.admin_find_result_line(p.name, p.size, p.chat_id),
                callback_data=f"adm:p:{p.chat_id}:{p.user_id}",
            )
        ]
        for p in results
    ]
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    await message.answer(
        texts.admin_find_found(len(results)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast_text)
    await _edit(callback, texts.ADMIN_ENTER_BCAST, None)


@router.message(AdminStates.broadcast_text)
async def msg_bcast(message: Message, state: FSMContext) -> None:
    text = message.html_text if message.text else None
    if not text:
        await state.clear()
        await message.answer(texts.ADMIN_BCAST_EMPTY, reply_markup=main_menu_kb())
        return
    await state.update_data(bcast_text=text)
    await state.set_state(AdminStates.broadcast_confirm)
    await message.answer(
        texts.admin_bcast_preview(text),
        reply_markup=_confirm_kb("adm:yes:bcast", "adm:home"),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm:yes:bcast")
async def cb_do_bcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    text = data.get("bcast_text")
    if not text:
        await _edit(callback, texts.ADMIN_BCAST_LOST, main_menu_kb())
        return
    if callback.message is not None:
        await callback.message.edit_text(texts.ADMIN_BCAST_STARTED)
    await callback.answer()
    targets = await admin_actions.broadcast_targets()
    sent = 0
    failed = 0
    for chat_id in targets:
        thread_id, reason = await threads_repo.resolve_thread(chat_id)
        ok = False
        try:
            await bot.send_message(
                chat_id, text, parse_mode="HTML", message_thread_id=thread_id
            )
            ok = True
        except Exception:
            if thread_id is not None:  # тема могла быть удалена — ретрай в General
                try:
                    await bot.send_message(chat_id, text, parse_mode="HTML")
                    ok = True
                except Exception:
                    ok = False
        sent += int(ok)
        failed += int(not ok)
        if ok and reason == "auto":
            try:
                await bot.send_message(
                    chat_id,
                    texts.ADMIN_BCAST_AUTO_TOPIC,
                    parse_mode="HTML",
                    message_thread_id=thread_id,
                )
            except Exception:
                pass
    if callback.message is not None:
        await callback.message.answer(
            texts.admin_bcast_done(sent, failed),
            reply_markup=main_menu_kb(),
        )
