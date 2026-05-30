"""In-Telegram global admin panel.

The UI is a thin layer over services.admin_actions (which are Telegram-agnostic
and reusable by a future web panel). Access is restricted to user ids listed in
the ADMIN_IDS environment variable (comma-separated).
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
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
from repositories import broadcasts as broadcasts_repo
from repositories import chats as chats_repo
from repositories import players as players_repo
from repositories import threads as threads_repo
from services import admin_actions
from services import global_settings
from services import settings_view
from services.admins import admin_ids
from services.global_settings import get_config

router = Router()

BCAST_MODES = ("all", "groups", "dm", "active")


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
    filter_chats = State()
    filter_players = State()


CHAT_SORT_CODES = {c for c, _ in texts.CHAT_SORTS}
PLAYER_SORT_CODES = {c for c, _ in texts.PLAYER_SORTS}


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
            [
                InlineKeyboardButton(
                    text=texts.BTN_BCAST_HISTORY, callback_data="adm:bhist:0"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_GLOBAL_SETTINGS, callback_data="adm:gset"
                ),
            ],
        ]
    )


def _back_row(parent_data: str) -> list[InlineKeyboardButton]:
    """A single-button row that navigates back to a parent screen. Parent
    context is encoded in the existing adm:* callback, so Back works even after
    FSM state is cleared or an old message is reopened."""
    return [InlineKeyboardButton(text=texts.BTN_BACK, callback_data=parent_data)]


def _pager(prefix: str, page: int, total: int, per_page: int) -> list[InlineKeyboardButton]:
    """One nav row: ⏮ « X/Y » ⏭. `prefix` already carries any sort/filter
    context; the target page is appended as the trailing segment. The middle
    indicator is inert (adm:noop). Returns [] when there is only one page."""
    pages = max(1, math.ceil(total / per_page)) if per_page > 0 else 1
    if pages <= 1:
        return []
    page = max(0, min(page, pages - 1))
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton(text=texts.BTN_FIRST, callback_data=f"{prefix}:0"))
        row.append(InlineKeyboardButton(text=texts.BTN_PREV, callback_data=f"{prefix}:{page - 1}"))
    row.append(
        InlineKeyboardButton(
            text=texts.pager_indicator(page, pages, total), callback_data="adm:noop"
        )
    )
    if page < pages - 1:
        row.append(InlineKeyboardButton(text=texts.BTN_NEXT, callback_data=f"{prefix}:{page + 1}"))
        row.append(InlineKeyboardButton(text=texts.BTN_LAST, callback_data=f"{prefix}:{pages - 1}"))
    return row


def _sort_row(
    options: list[tuple[str, str]], active: str, cb: Callable[[str], str]
) -> list[InlineKeyboardButton]:
    """A row of sort-toggle buttons; the active one is marked. `cb` maps a sort
    code to its callback_data (which resets to page 0 with that sort)."""
    return [
        InlineKeyboardButton(
            text=texts.sort_btn(label, code == active), callback_data=cb(code)
        )
        for code, label in options
    ]


def _filter_row(enter_data: str, clear_data: str | None) -> list[InlineKeyboardButton]:
    row = [InlineKeyboardButton(text=texts.BTN_FILTER, callback_data=enter_data)]
    if clear_data is not None:
        row.append(
            InlineKeyboardButton(text=texts.BTN_FILTER_CLEAR, callback_data=clear_data)
        )
    return row


async def render_chats(
    page: int, sort: str = "n", name_filter: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    per_page = (await get_config()).page_size
    total = await chats_repo.count_chats(name_filter)
    active = await chats_repo.active_chat_count(active_days=(await get_config()).active_days)
    offset = page * per_page
    chats = await chats_repo.list_chats_with_owner(
        offset=offset, limit=per_page, sort=sort, name_filter=name_filter
    )

    rows: list[list[InlineKeyboardButton]] = []
    for c, owner in chats:
        label = texts.admin_chat_label(
            c.type,
            c.title,
            owner.first_name if owner else None,
            owner.username if owner else None,
            c.chat_id,
        )
        flag = "🚫 " if c.is_banned else ""
        rows.append(
            [InlineKeyboardButton(text=f"{flag}{label}", callback_data=f"adm:chat:{c.chat_id}")]
        )

    rows.append(_sort_row(texts.CHAT_SORTS, sort, lambda s: f"adm:chats:{s}:0"))
    clear = f"adm:cfchats:{sort}" if name_filter else None
    rows.append(_filter_row(f"adm:fchats:{sort}", clear))

    nav = _pager(f"adm:chats:{sort}", page, total, per_page)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])

    text = texts.admin_chats_overview(total, active, page, name_filter)
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_chat(
    chat_id: int, page: int = 0, sort: str = "s", name_filter: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    per_page = (await get_config()).page_size
    chat = await chats_repo.get_chat(chat_id)
    stats = await players_repo.chat_player_stats(chat_id)
    banned_count = await players_repo.count_chat_banned(chat_id)
    total_players = await players_repo.count_players(chat_id, name_filter)
    offset = page * per_page
    players = await players_repo.list_players_page(
        chat_id, offset, per_page, sort=sort, name_filter=name_filter
    )

    if chat and chat.type == "private":
        owner = await chats_repo.get_user(chat_id)
        title = texts.admin_chat_label(
            chat.type, chat.title,
            owner.first_name if owner else None,
            owner.username if owner else None,
            chat_id,
        )
    else:
        title = (chat.title if chat and chat.title else str(chat_id))
    banned = chat and chat.is_banned
    lines = [
        texts.crumb("Чаты", title),
        texts.admin_chat_header(title, chat_id),
        texts.admin_chat_stats(stats['players'], stats['total_size'], stats['biggest']),
        texts.admin_chat_banned_count(banned_count),
    ]
    if name_filter:
        lines.append(texts.admin_filter_note(name_filter, total_players))
    lines.append("")
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
        _sort_row(texts.PLAYER_SORTS, sort, lambda s: f"adm:chat:{chat_id}:{s}:0")
    )
    clear = f"adm:cfchat:{chat_id}:{sort}" if name_filter else None
    rows.append(_filter_row(f"adm:fchat:{chat_id}:{sort}", clear))

    nav = _pager(f"adm:chat:{chat_id}:{sort}", page, total_players, per_page)
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(text=texts.BTN_RESET_CHAT, callback_data=f"adm:rchat:{chat_id}"),
            InlineKeyboardButton(
                text=texts.BTN_UNBAN if banned else texts.BTN_BAN_CHAT,
                callback_data=f"adm:{'uchat' if banned else 'bchat'}:{chat_id}",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_CHAT_SETTINGS, callback_data=f"adm:settings:{chat_id}"
            ),
            InlineKeyboardButton(
                text=texts.BTN_LOCAL_BANS, callback_data=f"adm:lban:{chat_id}:0"
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK_LIST, callback_data="adm:chats:0")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def render_player(
    chat_id: int, user_id: int, *, back_data: str | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    # When reached from search results, `back_data` points back at those results
    # (e.g. "adm:fp:0") so the admin returns to their search instead of being
    # dropped into the player's chat. Falls back to the chat view otherwise.
    if back_data is not None:
        back_btn = InlineKeyboardButton(text=texts.BTN_BACK_FIND, callback_data=back_data)
    else:
        back_btn = InlineKeyboardButton(
            text=texts.BTN_BACK_CHAT, callback_data=f"adm:chat:{chat_id}"
        )
    p = await players_repo.get_player(chat_id, user_id)
    if p is None:
        return texts.ADMIN_PLAYER_NOT_FOUND, InlineKeyboardMarkup(
            inline_keyboard=[[back_btn]]
        )
    user = await chats_repo.get_user(user_id)
    username = f"@{user.username}" if user and user.username else "—"
    tag = disease_tag(_player_dict(p))
    text = texts.admin_player_header(p.name, tag, user_id, username, p.size, chat_id)
    base = f"{chat_id}:{user_id}"
    if user and user.is_banned:
        ban_btn = InlineKeyboardButton(
            text=texts.BTN_UNBAN_USER, callback_data=f"adm:uuser:{chat_id}:{user_id}"
        )
    else:
        ban_btn = InlineKeyboardButton(
            text=texts.BTN_BAN_USER, callback_data=f"adm:buser:{chat_id}:{user_id}"
        )
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
            ban_btn,
            back_btn,
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
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest as e:
            # Re-clicking an already-active option re-renders identical content;
            # Telegram rejects it with "message is not modified". That's benign —
            # swallow it so the button stops spinning instead of erroring out.
            if "message is not modified" not in str(e).lower():
                raise
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


@router.callback_query(F.data == "adm:noop")
async def cb_noop(callback: CallbackQuery) -> None:
    # Inert page-indicator button in the pager row.
    await callback.answer()


def _parse_sort_page(rest: list[str], default_sort: str, valid: set[str]) -> tuple[str, int]:
    """Tolerantly parse the trailing [sort?][page?] segments of a list callback.
    Accepts the legacy bare-page form and the new sort+page form."""
    sort, page = default_sort, 0
    if len(rest) == 1:
        if rest[0].isdigit():
            page = int(rest[0])
        elif rest[0] in valid:
            sort = rest[0]
    elif len(rest) >= 2:
        if rest[0] in valid:
            sort = rest[0]
        page = int(rest[1]) if rest[1].isdigit() else 0
    return sort, page


@router.callback_query(F.data.startswith("adm:chats:"))
async def cb_chats(callback: CallbackQuery, state: FSMContext) -> None:
    sort, page = _parse_sort_page(callback.data.split(":")[2:], "n", CHAT_SORT_CODES)
    name_filter = (await state.get_data()).get("chats_filter")
    text, kb = await render_chats(page, sort, name_filter)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:fchats:"))
async def cb_filter_chats(callback: CallbackQuery, state: FSMContext) -> None:
    sort = callback.data.split(":")[2]
    await state.set_state(AdminStates.filter_chats)
    await state.update_data(filter_sort=sort)
    await _edit(callback, texts.ADMIN_ENTER_FILTER_CHATS, None)


@router.callback_query(F.data.startswith("adm:cfchats:"))
async def cb_clear_filter_chats(callback: CallbackQuery, state: FSMContext) -> None:
    sort = callback.data.split(":")[2]
    await state.update_data(chats_filter=None)
    await state.set_state(None)
    text, kb = await render_chats(0, sort, None)
    await _edit(callback, text, kb)


@router.message(AdminStates.filter_chats)
async def msg_filter_chats(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sort = data.get("filter_sort", "n")
    query = (message.text or "").strip()[: texts.MAX_QUERY_LEN]
    # Keep the filter in FSM data (no active input state) so pagination/sort can
    # re-apply it; an empty query clears it.
    await state.set_state(None)
    await state.update_data(chats_filter=query or None)
    text, kb = await render_chats(0, sort, query or None)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _player_filter_for(data: dict, chat_id: int) -> str | None:
    """Player filters are scoped to a chat so they don't leak between chats."""
    if data.get("players_filter_chat") == chat_id:
        return data.get("players_filter")
    return None


@router.callback_query(F.data.startswith("adm:chat:"))
async def cb_chat(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    sort, page = _parse_sort_page(parts[3:], "s", PLAYER_SORT_CODES)
    name_filter = _player_filter_for(await state.get_data(), chat_id)
    text, kb = await render_chat(chat_id, page, sort, name_filter)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:fchat:"))
async def cb_filter_players(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    chat_id, sort = int(parts[2]), parts[3]
    await state.set_state(AdminStates.filter_players)
    await state.update_data(filter_chat=chat_id, filter_sort=sort)
    await _edit(callback, texts.ADMIN_ENTER_FILTER_PLAYERS, None)


@router.callback_query(F.data.startswith("adm:cfchat:"))
async def cb_clear_filter_players(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    chat_id, sort = int(parts[2]), parts[3]
    await state.update_data(players_filter=None, players_filter_chat=None)
    await state.set_state(None)
    text, kb = await render_chat(chat_id, 0, sort, None)
    await _edit(callback, text, kb)


@router.message(AdminStates.filter_players)
async def msg_filter_players(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("filter_chat")
    sort = data.get("filter_sort", "s")
    if chat_id is None:
        await state.clear()
        return
    query = (message.text or "").strip()[: texts.MAX_QUERY_LEN]
    await state.set_state(None)
    await state.update_data(
        players_filter=query or None, players_filter_chat=chat_id if query else None
    )
    text, kb = await render_chat(chat_id, 0, sort, query or None)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:settings:"))
async def cb_chat_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    chat_id = int(callback.data.split(":")[2])
    text, kb = await settings_view.render_settings(chat_id, scope="global")
    await _edit(callback, text, kb)


async def render_local_bans(chat_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    per_page = (await get_config()).page_size
    total = await players_repo.count_chat_banned(chat_id)
    offset = page * per_page
    banned = await players_repo.list_chat_banned(chat_id, offset, per_page)

    lines = [texts.admin_local_bans_page(total, page)]
    rows: list[list[InlineKeyboardButton]] = []
    for p in banned:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.admin_local_unban_btn(p.name),
                    callback_data=f"adm:lunban:{chat_id}:{p.user_id}",
                )
            ]
        )
    if not banned:
        lines.append("")
        lines.append(texts.ADMIN_NO_LOCAL_BANS)

    nav = _pager(f"adm:lban:{chat_id}", page, total, per_page)
    if nav:
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton(text=texts.BTN_BACK_CHAT, callback_data=f"adm:chat:{chat_id}")]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("adm:lban:"))
async def cb_local_bans(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    chat_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    text, kb = await render_local_bans(chat_id, page)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:lunban:"))
async def cb_local_unban(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    chat_id, user_id = int(parts[2]), int(parts[3])
    res = await admin_actions.local_unban(callback.from_user.id, chat_id, user_id)
    text, kb = await render_local_bans(chat_id, 0)
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:p:"))
async def cb_player(callback: CallbackQuery, state: FSMContext) -> None:
    # Reaching a player view means leaving any input flow (e.g. cancelling the
    # ban reason/duration picker), so drop any half-finished FSM state.
    await state.clear()
    _, _, chat_id, user_id = callback.data.split(":")
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:pf:"))
async def cb_player_from_find(callback: CallbackQuery, state: FSMContext) -> None:
    # Same player view, but reached from search results. Keep find_query_text in
    # FSM data (only drop the active input state) so the player's Back button can
    # return to the results via adm:fp:0.
    await state.set_state(None)
    _, _, chat_id, user_id = callback.data.split(":")
    text, kb = await render_player(int(chat_id), int(user_id), back_data="adm:fp:0")
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
    reason = (message.text or "").strip()[: texts.MAX_BAN_REASON_LEN]
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


@router.callback_query(F.data.startswith("adm:uuser:"))
async def cb_unban_user(callback: CallbackQuery) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    res = await admin_actions.unban_user(callback.from_user.id, int(user_id))
    text, kb = await render_player(int(chat_id), int(user_id))
    await _edit(callback, text, kb)
    await callback.answer(res.message, show_alert=True)


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
    active = await chats_repo.active_chat_count(active_days=(await get_config()).active_days)
    text = texts.admin_global_stats(
        s['chats'], s['users'], s['players'], s['total_size'], active
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")]]
    )
    await _edit(callback, text, kb)


# ---- global tunables panel (global admins only) ----


async def render_gset() -> tuple[str, InlineKeyboardMarkup]:
    cfg = await get_config()
    rows: list[list[InlineKeyboardButton]] = []
    for key, label, small, big, _mn, _mx in global_settings.EDITABLE:
        val = getattr(cfg, key)
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.gset_field_label(label, val), callback_data="adm:noop"
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text=f"−{big}", callback_data=f"adm:gadj:{key}:{-big}"),
                InlineKeyboardButton(text=f"−{small}", callback_data=f"adm:gadj:{key}:{-small}"),
                InlineKeyboardButton(text=f"+{small}", callback_data=f"adm:gadj:{key}:{small}"),
                InlineKeyboardButton(text=f"+{big}", callback_data=f"adm:gadj:{key}:{big}"),
            ]
        )
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return texts.ADMIN_GSET_TITLE, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm:gset")
async def cb_gset(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_gset()
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:gadj:"))
async def cb_gadj(callback: CallbackQuery) -> None:
    _, _, key, delta = callback.data.split(":")
    try:
        await global_settings.adjust(key, int(delta))
    except KeyError:
        await callback.answer()
        return
    text, kb = await render_gset()
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
    name = (message.text or "").strip()[: texts.MAX_NAME_LEN]
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


async def render_find(query: str, page: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Build a paginated find-results screen, or None when nothing matches.
    Paginated in the DB so results beyond the first page are reachable."""
    total = await players_repo.count_find_players(query)
    if total == 0:
        return None
    per_page = (await get_config()).page_size
    offset = page * per_page
    window = await players_repo.find_players_page(query, offset, per_page)
    rows = [
        [
            InlineKeyboardButton(
                text=texts.admin_find_result_line(p.name, p.size, p.chat_id),
                callback_data=f"adm:pf:{p.chat_id}:{p.user_id}",
            )
        ]
        for p in window
    ]
    nav = _pager("adm:fp", page, total, per_page)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return texts.admin_find_found(total), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(AdminStates.find_query)
async def msg_find(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()[: texts.MAX_QUERY_LEN]
    if not query:
        await state.clear()
        await message.answer(texts.ADMIN_FIND_EMPTY, reply_markup=main_menu_kb())
        return
    rendered = await render_find(query, 0)
    if rendered is None:
        await state.clear()
        await message.answer(texts.ADMIN_FIND_NONE, reply_markup=main_menu_kb())
        return
    # Keep the query in FSM data (without an active input state) so page nav can
    # re-run the search. If state is later cleared, nav falls back gracefully.
    await state.set_state(None)
    await state.update_data(find_query_text=query)
    text, kb = rendered
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("adm:fp:"))
async def cb_find_page(callback: CallbackQuery, state: FSMContext) -> None:
    page = int(callback.data.split(":")[2])
    query = (await state.get_data()).get("find_query_text")
    if not query:
        await _edit(callback, texts.ADMIN_FIND_LOST, main_menu_kb())
        return
    rendered = await render_find(query, page)
    if rendered is None:
        await _edit(callback, texts.ADMIN_FIND_NONE, main_menu_kb())
        return
    text, kb = rendered
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast_text)
    await _edit(callback, texts.ADMIN_ENTER_BCAST, None)


def _bcast_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_MODE_ALL, callback_data="adm:bmode:all")],
            [InlineKeyboardButton(text=texts.BTN_MODE_GROUPS, callback_data="adm:bmode:groups")],
            [InlineKeyboardButton(text=texts.BTN_MODE_DM, callback_data="adm:bmode:dm")],
            [InlineKeyboardButton(text=texts.BTN_MODE_ACTIVE, callback_data="adm:bmode:active")],
            [InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="adm:home")],
        ]
    )


@router.message(AdminStates.broadcast_text)
async def msg_bcast(message: Message, state: FSMContext) -> None:
    text = message.html_text if message.text else None
    if not text:
        await state.clear()
        await message.answer(texts.ADMIN_BCAST_EMPTY, reply_markup=main_menu_kb())
        return
    await state.update_data(bcast_text=text)
    await state.set_state(AdminStates.broadcast_confirm)
    await message.answer(texts.ADMIN_PICK_BCAST_MODE, reply_markup=_bcast_mode_kb())


@router.callback_query(F.data == "adm:bpick")
async def cb_bcast_pick(callback: CallbackQuery, state: FSMContext) -> None:
    text = (await state.get_data()).get("bcast_text")
    if not text:
        await _edit(callback, texts.ADMIN_BCAST_LOST, main_menu_kb())
        return
    await _edit(callback, texts.ADMIN_PICK_BCAST_MODE, _bcast_mode_kb())


@router.callback_query(F.data.startswith("adm:bmode:"))
async def cb_bcast_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":")[2]
    if mode not in BCAST_MODES:
        mode = "all"
    data = await state.get_data()
    text = data.get("bcast_text")
    if not text:
        await _edit(callback, texts.ADMIN_BCAST_LOST, main_menu_kb())
        return
    await state.update_data(bcast_mode=mode)
    targets = await admin_actions.broadcast_targets(
        mode, active_days=(await get_config()).active_days
    )
    await _edit(
        callback,
        texts.admin_bcast_mode_preview(text, texts.bcast_mode_label(mode), len(targets)),
        _confirm_kb("adm:yes:bcast", "adm:bpick"),
    )


async def _bcast_send(bot: Bot, chat_id: int, text: str, thread_id: int | None) -> bool:
    """Send one broadcast message, honoring Telegram flood limits (RetryAfter).
    Returns True on success, False on any other failure."""
    for _ in range(2):
        try:
            await bot.send_message(
                chat_id, text, parse_mode="HTML", message_thread_id=thread_id
            )
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception:
            return False
    return False


@router.callback_query(F.data == "adm:yes:bcast")
async def cb_do_bcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    text = data.get("bcast_text")
    if not text:
        await _edit(callback, texts.ADMIN_BCAST_LOST, main_menu_kb())
        return
    mode = data.get("bcast_mode", "all")
    cfg = await get_config()
    if callback.message is not None:
        await callback.message.edit_text(texts.ADMIN_BCAST_STARTED)
    await callback.answer()
    targets = await admin_actions.broadcast_targets(mode, active_days=cfg.active_days)
    # Pause between sends to stay under Telegram's ~30 msg/s flood cap.
    rate_delay = cfg.bcast_rate_delay
    sent = 0
    failed = 0
    for chat_id in targets:
        thread_id, reason = await threads_repo.resolve_thread(chat_id)
        ok = await _bcast_send(bot, chat_id, text, thread_id)
        if not ok and thread_id is not None:  # тема могла быть удалена — ретрай в General
            ok = await _bcast_send(bot, chat_id, text, None)
        sent += int(ok)
        failed += int(not ok)
        if ok and reason == "auto":
            await _bcast_send(bot, chat_id, texts.ADMIN_BCAST_AUTO_TOPIC, thread_id)
        await asyncio.sleep(rate_delay)
    await admin_actions.log_broadcast(
        callback.from_user.id, text[: texts.MAX_BCAST_PREVIEW_LEN], mode, sent, failed
    )
    if callback.message is not None:
        await callback.message.answer(
            texts.admin_bcast_done(sent, failed),
            reply_markup=main_menu_kb(),
        )


async def render_bcast_history(page: int) -> tuple[str, InlineKeyboardMarkup]:
    per_page = (await get_config()).page_size
    total = await broadcasts_repo.count_broadcasts()
    offset = page * per_page
    rows_db = await broadcasts_repo.list_broadcasts(offset=offset, limit=per_page)

    lines = [texts.admin_bcast_history_page(total, page), ""]
    if rows_db:
        lines.extend(
            texts.admin_bcast_history_line(b.created_at, b.target_mode, b.sent, b.failed, b.preview)
            for b in rows_db
        )
    else:
        lines.append(texts.ADMIN_BCAST_NO_HISTORY)

    nav = _pager("adm:bhist", page, total, per_page)
    kb_rows: list[list[InlineKeyboardButton]] = []
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows)


@router.callback_query(F.data.startswith("adm:bhist:"))
async def cb_bcast_history(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    page = int(callback.data.split(":")[2])
    text, kb = await render_bcast_history(page)
    await _edit(callback, text, kb)
