"""In-Telegram global admin panel.

The UI is a thin layer over services.admin_actions (which are Telegram-agnostic
and reusable by a future web panel). Access is restricted to user ids listed in
the ADMIN_IDS environment variable (comma-separated).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
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
from services import admin_actions, casino, economy_metrics, global_settings, settings_view
from services.admins import admin_ids
from services.global_settings import get_config

router = Router()
logger = logging.getLogger(__name__)


class IsGlobalAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user and user.id in admin_ids())


router.message.filter(IsGlobalAdmin())
router.callback_query.filter(IsGlobalAdmin())


class AdminStates(StatesGroup):
    set_size = State()
    set_name = State()
    set_public_label = State()
    find_query = State()
    broadcast_text = State()
    broadcast_user = State()
    broadcast_chat = State()
    ban_reason = State()
    ban_duration = State()
    filter_chats = State()
    filter_players = State()
    casino_payout_edit = State()


CHAT_SORT_CODES = {c for c, _ in texts.CHAT_SORTS}
PLAYER_SORT_CODES = {c for c, _ in texts.PLAYER_SORTS}
CASINO_PAYOUTS_PER_PAGE = 8
CASINO_DRAFT_KEY = "casino_payout_draft"
CASINO_SYMBOL_CODES = {
    "b": casino.BAR,
    "g": casino.GRAPES,
    "l": casino.LEMON,
    "s": casino.SEVEN,
}


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
                InlineKeyboardButton(text=texts.BTN_BCAST_HISTORY, callback_data="adm:bhist:0"),
            ],
            [
                InlineKeyboardButton(text=texts.BTN_ECONOMY, callback_data="adm:economy"),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_CASINO_PAYOUTS,
                    callback_data="adm:cpay:0",
                ),
            ],
            [
                InlineKeyboardButton(text=texts.BTN_GLOBAL_SETTINGS, callback_data="adm:gset"),
            ],
            [
                InlineKeyboardButton(text=texts.BTN_GSET_BANK, callback_data="adm:gsetbank"),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_GSET_INSURANCE, callback_data="adm:gsetinsurance"
                ),
            ],
            [InlineKeyboardButton(text=texts.BTN_GSET_CORP, callback_data="adm:gsetcorp")],
            [InlineKeyboardButton(text="📈 Подробная аналитика", callback_data="adm:astats:g:0:0")],
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
        InlineKeyboardButton(text=texts.sort_btn(label, code == active), callback_data=cb(code))
        for code, label in options
    ]


def _filter_row(enter_data: str, clear_data: str | None) -> list[InlineKeyboardButton]:
    row = [InlineKeyboardButton(text=texts.BTN_FILTER, callback_data=enter_data)]
    if clear_data is not None:
        row.append(InlineKeyboardButton(text=texts.BTN_FILTER_CLEAR, callback_data=clear_data))
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

    title = chat.title if chat and chat.title else str(chat_id)
    banned = chat and chat.is_banned
    lines = [
        texts.crumb("Чаты", title),
        texts.admin_chat_header(title, chat_id),
        texts.admin_chat_stats(stats["players"], stats["total_size"], stats["biggest"]),
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

    rows.append(_sort_row(texts.PLAYER_SORTS, sort, lambda s: f"adm:chat:{chat_id}:{s}:0"))
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
                text=texts.BTN_HEALTH_REFORM,
                callback_data=f"adm:reform:{chat_id}",
            ),
            InlineKeyboardButton(text=texts.BTN_CORP, callback_data=f"adm:corp:{chat_id}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_CHAT_SETTINGS, callback_data=f"adm:settings:{chat_id}"
            ),
            InlineKeyboardButton(text=texts.BTN_LOCAL_BANS, callback_data=f"adm:lban:{chat_id}:0"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="📈 Аналитика чата", callback_data=f"adm:astats:c:{chat_id}:0")]
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
        return texts.ADMIN_PLAYER_NOT_FOUND, InlineKeyboardMarkup(inline_keyboard=[[back_btn]])
    user = await chats_repo.get_user(user_id)
    username = f"@{user.username}" if user and user.username else "—"
    tag = disease_tag(_player_dict(p))
    text = texts.admin_player_header(
        p.name,
        tag,
        user_id,
        username,
        p.size,
        chat_id,
        user.public_label if user else None,
    )
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
            InlineKeyboardButton(
                text=texts.BTN_SET_PUBLIC_LABEL,
                callback_data=f"adm:setlabel:{base}",
            )
        ],
        [
            InlineKeyboardButton(text=texts.BTN_RESET_PLAYER, callback_data=f"adm:rp:{base}"),
            InlineKeyboardButton(text=texts.BTN_DELETE_PLAYER, callback_data=f"adm:del:{base}"),
        ],
        [
            InlineKeyboardButton(
                text="📈 Полная статистика игрока",
                callback_data=f"adm:astats:u:{chat_id}:{user_id}",
            )
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


@router.callback_query(F.data.startswith("adm:astats:"))
async def cb_detailed_stats(callback: CallbackQuery) -> None:
    from handlers.stats import send_panel
    from services import analytics

    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        _, _, code, chat_id, user_id = (callback.data or "").split(":")
        scope = analytics.Scope(
            {"u": "user", "c": "chat", "g": "global"}[code],
            int(chat_id),
            int(user_id),
        )
    except (KeyError, ValueError):
        await callback.answer(texts.CALLBACK_INVALID, show_alert=True)
        return
    await send_panel(callback.message, scope, code)
    await callback.answer()


# Shown on every text-input prompt so an accidental tap never strands the admin
# in an input state with no way out but to type something.
_CANCEL_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="adm:home")]]
)


def _public_label_cancel_kb(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_CANCEL,
                    callback_data=f"adm:p:{chat_id}:{user_id}",
                )
            ]
        ]
    )


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
        [InlineKeyboardButton(text=txt, callback_data=f"adm:bur:{chat_id}:{user_id}:{rid}")]
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
        [InlineKeyboardButton(text=texts.BTN_OWN_REASON, callback_data=f"adm:bcrc:{chat_id}")]
    )
    rows.append([InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=f"adm:chat:{chat_id}")])
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
    except TelegramAPIError:
        logger.info("Could not deliver ban notice to user")


async def _notify_chat_banned(bot: Bot, chat_id: int, reason: str | None) -> None:
    """Best-effort in-chat notice that the chat was banned."""
    suffix = texts.ban_reason_suffix(reason)
    try:
        await bot.send_message(chat_id, texts.notify_chat_banned(suffix))
    except TelegramAPIError:
        logger.info("Could not deliver chat ban notice")


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
    await _edit(callback, texts.ADMIN_ENTER_FILTER_CHATS, _CANCEL_KB)


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
    await _edit(callback, texts.ADMIN_ENTER_FILTER_PLAYERS, _CANCEL_KB)


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
    chat_id, user_id, reason_id, dur_id = (int(parts[2]), int(parts[3]), parts[4], parts[5])
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
    await _edit(callback, texts.ADMIN_ENTER_BAN_REASON, _CANCEL_KB)


@router.callback_query(F.data.startswith("adm:burxc:"))
async def cb_ban_user_apply_custom(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
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


@router.callback_query(F.data.startswith("adm:reform:"))
async def cb_health_reform(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    chat = await chats_repo.get_chat(chat_id)
    title = chat.title if chat and chat.title else str(chat_id)
    result = await admin_actions.preview_health_reform(chat_id)
    if result.already_applied:
        kb = InlineKeyboardMarkup(inline_keyboard=[_back_row(f"adm:chat:{chat_id}")])
    else:
        kb = _confirm_kb(
            f"adm:yes:reform:{chat_id}",
            f"adm:chat:{chat_id}",
        )
    await _edit(callback, texts.admin_health_reform_preview(title, result), kb)


@router.callback_query(F.data.startswith("adm:yes:reform:"))
async def cb_do_health_reform(callback: CallbackQuery, bot: Bot) -> None:
    chat_id = int(callback.data.split(":")[3])
    result = await admin_actions.apply_health_reform(callback.from_user.id, chat_id)
    announced = False
    if not result.already_applied:
        announced, _, _ = await _send_to_resolved_thread(
            bot,
            chat_id,
            texts.health_reform_announcement(result),
        )
    text, kb = await render_chat(chat_id)
    await _edit(callback, text, kb)
    if result.already_applied:
        await callback.answer("Комиссия уже провела сушку в этом чате.", show_alert=True)
    else:
        await callback.answer(
            texts.admin_health_reform_done(result, announced),
            show_alert=True,
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
    await _edit(callback, texts.ADMIN_ENTER_BAN_CHAT_REASON, _CANCEL_KB)


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
        s["chats"], s["users"], s["players"], s["total_size"], active, s["dm_users"]
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")]]
    )
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:economy")
async def cb_economy(callback: CallbackQuery) -> None:
    report = await economy_metrics.snapshot()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")]]
    )
    await _edit(callback, texts.admin_economy(report), kb)


# ---- per-chat Corporation control ----

CORP_DELTAS = (10, 50)


async def render_corp(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Corporation screen for one chat: balances, liabilities and the cash knobs."""
    cfg = await get_config()
    snapshot = await admin_actions.corp_snapshot(chat_id)
    if snapshot is None:
        return texts.RES_CORP_NOT_FOUND, InlineKeyboardMarkup(
            inline_keyboard=[_back_row("adm:chats:0")]
        )
    rows = [
        [
            InlineKeyboardButton(text=f"➕{value}", callback_data=f"adm:corpadj:{chat_id}:{value}")
            for value in CORP_DELTAS
        ],
        [
            InlineKeyboardButton(text=f"➖{value}", callback_data=f"adm:corpadj:{chat_id}:{-value}")
            for value in CORP_DELTAS
        ],
        [
            InlineKeyboardButton(
                text="🔄 Пересчитать статус", callback_data=f"adm:corpsync:{chat_id}"
            )
        ],
        _back_row(f"adm:chat:{chat_id}:s:0"),
    ]
    return (
        texts.admin_corp(snapshot, cfg.corp_recovery_liability_pct),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("adm:corp:"))
async def cb_corp(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    chat_id = int(callback.data.split(":")[2])
    text, kb = await render_corp(chat_id)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:corpadj:"))
async def cb_corp_adjust(callback: CallbackQuery) -> None:
    _, _, raw_chat, raw_delta = callback.data.split(":")
    chat_id = int(raw_chat)
    result = await admin_actions.adjust_corp_balance(callback.from_user.id, chat_id, int(raw_delta))
    text, kb = await render_corp(chat_id)
    await _edit(callback, text, kb)
    if not result.ok:
        await callback.answer(result.message, show_alert=True)


@router.callback_query(F.data.startswith("adm:corpsync:"))
async def cb_corp_sync(callback: CallbackQuery) -> None:
    chat_id = int(callback.data.split(":")[2])
    result = await admin_actions.recompute_corp_status(callback.from_user.id, chat_id)
    text, kb = await render_corp(chat_id)
    await _edit(callback, text, kb)
    await callback.answer(
        texts.RES_CORP_NOT_FOUND if result is None else result.message,
        show_alert=True,
    )


# ---- configurable global casino payout rules ----


def _casino_rule_button(rule) -> str:
    symbols = casino.decode_slot(rule.slot_value)
    payout = texts.casino_payout_label(rule.payout_kind, rule.payout_value)
    return f"{texts.casino_combination(symbols)} — {payout}"


async def render_casino_payouts(page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    total = await casino.count_payout_rules()
    pages = max(1, math.ceil(total / CASINO_PAYOUTS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    rules = await casino.list_payout_rules(
        offset=page * CASINO_PAYOUTS_PER_PAGE,
        limit=CASINO_PAYOUTS_PER_PAGE,
    )
    rows = [
        [
            InlineKeyboardButton(
                text=_casino_rule_button(rule),
                callback_data=f"adm:cpedit:{rule.slot_value}:{page}",
            )
        ]
        for rule in rules
    ]
    nav = _pager("adm:cpay", page, total, CASINO_PAYOUTS_PER_PAGE)
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=f"adm:cpnew:{page}")])
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return texts.admin_casino_payouts(total, page, pages), InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def _casino_draft(data: dict) -> dict | None:
    draft = data.get(CASINO_DRAFT_KEY)
    if not isinstance(draft, dict):
        return None
    symbols = draft.get("symbols")
    payout_kind = draft.get("payout_kind")
    payout_value = draft.get("payout_value")
    original = draft.get("original_slot_value")
    page = draft.get("page")
    if (
        not isinstance(symbols, list)
        or len(symbols) != 3
        or payout_kind not in casino.PAYOUT_KINDS
        or isinstance(payout_value, bool)
        or not isinstance(payout_value, int)
        or not 0
        <= payout_value
        <= (casino.MAX_MULTIPLIER if payout_kind == "multiplier" else casino.MAX_FIXED_PAYOUT)
        or (original is not None and not isinstance(original, int))
        or isinstance(original, bool)
        or isinstance(page, bool)
        or not isinstance(page, int)
    ):
        return None
    try:
        casino.encode_slot(tuple(symbols))
        if original is not None:
            casino.decode_slot(original)
    except casino.CasinoError:
        return None
    return draft


async def _get_casino_draft(callback: CallbackQuery, state: FSMContext) -> dict | None:
    draft = _casino_draft(await state.get_data())
    if draft is None:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
    return draft


async def _save_casino_draft(state: FSMContext, draft: dict) -> None:
    await state.update_data(**{CASINO_DRAFT_KEY: draft})


def render_casino_payout_editor(draft: dict) -> tuple[str, InlineKeyboardMarkup]:
    symbols = tuple(draft["symbols"])
    payout_kind = draft["payout_kind"]
    payout_value = draft["payout_value"]
    original = draft["original_slot_value"]
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"{position + 1}: {texts.CASINO_SYMBOL_LABELS[symbol]}",
                callback_data=f"adm:cpreel:{position}",
            )
            for position, symbol in enumerate(symbols)
        ],
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if payout_kind == 'multiplier' else ''}× ставка",
                callback_data="adm:cpkind:m",
            ),
            InlineKeyboardButton(
                text=f"{'✅ ' if payout_kind == 'fixed' else ''}фикс. см",
                callback_data="adm:cpkind:f",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"Выплата: {texts.casino_payout_label(payout_kind, payout_value)}",
                callback_data="adm:noop",
            )
        ],
    ]
    steps = (10, 1) if payout_kind == "multiplier" else (100, 10)
    rows.append(
        [
            InlineKeyboardButton(text=f"−{steps[0]}", callback_data=f"adm:cpadj:{-steps[0]}"),
            InlineKeyboardButton(text=f"−{steps[1]}", callback_data=f"adm:cpadj:{-steps[1]}"),
            InlineKeyboardButton(text=f"+{steps[1]}", callback_data=f"adm:cpadj:{steps[1]}"),
            InlineKeyboardButton(text=f"+{steps[0]}", callback_data=f"adm:cpadj:{steps[0]}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="✅ Сохранить", callback_data="adm:cpsave"),
            InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="adm:cpcancel"),
        ]
    )
    if original is not None:
        rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data="adm:cpdelask")])
    return texts.admin_casino_rule_editor(
        is_new=original is None,
        symbols=symbols,
        payout_kind=payout_kind,
        payout_value=payout_value,
    ), InlineKeyboardMarkup(inline_keyboard=rows)


def render_casino_symbol_picker(position: int) -> tuple[str, InlineKeyboardMarkup]:
    items = list(CASINO_SYMBOL_CODES.items())
    rows = [
        [
            InlineKeyboardButton(
                text=texts.CASINO_SYMBOL_LABELS[symbol],
                callback_data=f"adm:cpsym:{position}:{code}",
            )
            for code, symbol in items[start : start + 2]
        ]
        for start in range(0, len(items), 2)
    ]
    rows.append([InlineKeyboardButton(text=texts.BTN_BACK, callback_data="adm:cpbackedit")])
    return texts.admin_casino_symbol_picker(position), InlineKeyboardMarkup(inline_keyboard=rows)


async def _start_casino_editor(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    original_slot_value: int | None,
    symbols: tuple[str, str, str],
    payout_kind: str,
    payout_value: int,
    page: int,
) -> None:
    draft = {
        "original_slot_value": original_slot_value,
        "symbols": list(symbols),
        "payout_kind": payout_kind,
        "payout_value": payout_value,
        "page": max(0, page),
    }
    await state.clear()
    await state.set_state(AdminStates.casino_payout_edit)
    await _save_casino_draft(state, draft)
    text, kb = render_casino_payout_editor(draft)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:cpay:"))
async def cb_casino_payouts(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    await state.clear()
    text, kb = await render_casino_payouts(page)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:cpnew:"))
async def cb_casino_payout_new(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    used = {rule.slot_value for rule in await casino.list_payout_rules()}
    slot_value = next((value for value in range(1, 65) if value not in used), None)
    if slot_value is None:
        await callback.answer(texts.ADMIN_CASINO_RULES_FULL, show_alert=True)
        return
    await _start_casino_editor(
        callback,
        state,
        original_slot_value=None,
        symbols=casino.decode_slot(slot_value),
        payout_kind="multiplier",
        payout_value=1,
        page=page,
    )


@router.callback_query(F.data.startswith("adm:cpedit:"))
async def cb_casino_payout_edit(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _, _, raw_slot, raw_page = (callback.data or "").split(":")
        slot_value, page = int(raw_slot), int(raw_page)
        casino.decode_slot(slot_value)
    except (ValueError, casino.CasinoError):
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    rule = await casino.get_payout_rule(slot_value)
    if rule is None:
        await callback.answer(texts.ADMIN_CASINO_RULE_MISSING, show_alert=True)
        return
    await _start_casino_editor(
        callback,
        state,
        original_slot_value=slot_value,
        symbols=casino.decode_slot(slot_value),
        payout_kind=rule.payout_kind,
        payout_value=rule.payout_value,
        page=page,
    )


@router.callback_query(F.data.startswith("adm:cpreel:"))
async def cb_casino_payout_reel(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    try:
        position = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        position = -1
    if position not in {0, 1, 2}:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    text, kb = render_casino_symbol_picker(position)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:cpsym:"))
async def cb_casino_payout_symbol(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    try:
        _, _, raw_position, code = (callback.data or "").split(":")
        position = int(raw_position)
        symbol = CASINO_SYMBOL_CODES[code]
    except (KeyError, ValueError):
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    if position not in {0, 1, 2}:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    draft["symbols"][position] = symbol
    await _save_casino_draft(state, draft)
    text, kb = render_casino_payout_editor(draft)
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:cpbackedit")
async def cb_casino_payout_back(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    text, kb = render_casino_payout_editor(draft)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:cpkind:"))
async def cb_casino_payout_kind(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    code = (callback.data or "").split(":")[-1]
    payout_kind = {"m": "multiplier", "f": "fixed"}.get(code)
    if payout_kind is None:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    maximum = casino.MAX_MULTIPLIER if payout_kind == "multiplier" else casino.MAX_FIXED_PAYOUT
    draft["payout_kind"] = payout_kind
    draft["payout_value"] = min(draft["payout_value"], maximum)
    await _save_casino_draft(state, draft)
    text, kb = render_casino_payout_editor(draft)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:cpadj:"))
async def cb_casino_payout_adjust(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    try:
        delta = int((callback.data or "").split(":")[2])
    except (IndexError, ValueError):
        delta = 0
    allowed = {"multiplier": {-10, -1, 1, 10}, "fixed": {-100, -10, 10, 100}}
    payout_kind = draft["payout_kind"]
    if delta not in allowed[payout_kind]:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    maximum = casino.MAX_MULTIPLIER if payout_kind == "multiplier" else casino.MAX_FIXED_PAYOUT
    draft["payout_value"] = max(0, min(maximum, draft["payout_value"] + delta))
    await _save_casino_draft(state, draft)
    text, kb = render_casino_payout_editor(draft)
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:cpsave")
async def cb_casino_payout_save(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    slot_value = casino.encode_slot(tuple(draft["symbols"]))
    try:
        await casino.save_payout_rule(
            original_slot_value=draft["original_slot_value"],
            slot_value=slot_value,
            payout_kind=draft["payout_kind"],
            payout_value=draft["payout_value"],
        )
    except casino.CasinoError as error:
        message = (
            texts.ADMIN_CASINO_RULE_DUPLICATE
            if error.code == "duplicate_payout_rule"
            else texts.ADMIN_CASINO_RULE_MISSING
        )
        await callback.answer(message, show_alert=True)
        return
    page = draft["page"]
    await state.clear()
    text, kb = await render_casino_payouts(page)
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:cpcancel")
async def cb_casino_payout_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    page = draft["page"]
    await state.clear()
    text, kb = await render_casino_payouts(page)
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:cpdelask")
async def cb_casino_payout_delete_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    original = draft["original_slot_value"]
    if original is None:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.BTN_YES, callback_data="adm:cpdelete"),
                InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data="adm:cpbackedit"),
            ]
        ]
    )
    await _edit(callback, texts.admin_casino_delete_confirm(casino.decode_slot(original)), kb)


@router.callback_query(F.data == "adm:cpdelete")
async def cb_casino_payout_delete(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _get_casino_draft(callback, state)
    if draft is None:
        return
    original = draft["original_slot_value"]
    if original is None:
        await callback.answer(texts.ADMIN_CASINO_EDITOR_EXPIRED, show_alert=True)
        return
    await casino.delete_payout_rule(original)
    page = draft["page"]
    await state.clear()
    text, kb = await render_casino_payouts(page)
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


async def render_gset_bank() -> tuple[str, InlineKeyboardMarkup]:
    """Same shape as render_gset but for the banking knobs, kept on a separate
    panel so the combined button count stays under Telegram's 100-button cap."""
    cfg = await get_config()
    rows: list[list[InlineKeyboardButton]] = []
    for key, label, small, big, _mn, _mx in global_settings.EDITABLE_BANK:
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
                InlineKeyboardButton(text=f"−{big}", callback_data=f"adm:gadjb:{key}:{-big}"),
                InlineKeyboardButton(text=f"−{small}", callback_data=f"adm:gadjb:{key}:{-small}"),
                InlineKeyboardButton(text=f"+{small}", callback_data=f"adm:gadjb:{key}:{small}"),
                InlineKeyboardButton(text=f"+{big}", callback_data=f"adm:gadjb:{key}:{big}"),
            ]
        )
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return texts.ADMIN_GSET_BANK_TITLE, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_gset_insurance() -> tuple[str, InlineKeyboardMarkup]:
    cfg = await get_config()
    rows: list[list[InlineKeyboardButton]] = []
    for key, label, small, big, _mn, _mx in global_settings.EDITABLE_INSURANCE:
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
                InlineKeyboardButton(text=f"−{big}", callback_data=f"adm:gadji:{key}:{-big}"),
                InlineKeyboardButton(text=f"−{small}", callback_data=f"adm:gadji:{key}:{-small}"),
                InlineKeyboardButton(text=f"+{small}", callback_data=f"adm:gadji:{key}:{small}"),
                InlineKeyboardButton(text=f"+{big}", callback_data=f"adm:gadji:{key}:{big}"),
            ]
        )
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return texts.ADMIN_GSET_INSURANCE_TITLE, InlineKeyboardMarkup(inline_keyboard=rows)


async def render_gset_corp() -> tuple[str, InlineKeyboardMarkup]:
    cfg = await get_config()
    rows: list[list[InlineKeyboardButton]] = []
    for key, label, small, big, _mn, _mx in global_settings.EDITABLE_CORP:
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
                InlineKeyboardButton(text=f"−{big}", callback_data=f"adm:gadjc:{key}:{-big}"),
                InlineKeyboardButton(text=f"−{small}", callback_data=f"adm:gadjc:{key}:{-small}"),
                InlineKeyboardButton(text=f"+{small}", callback_data=f"adm:gadjc:{key}:{small}"),
                InlineKeyboardButton(text=f"+{big}", callback_data=f"adm:gadjc:{key}:{big}"),
            ]
        )
    rows.append([InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")])
    return texts.ADMIN_GSET_CORP_TITLE, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm:gsetbank")
async def cb_gset_bank(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_gset_bank()
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:gadjb:"))
async def cb_gadj_bank(callback: CallbackQuery) -> None:
    _, _, key, delta = callback.data.split(":")
    try:
        await global_settings.adjust(key, int(delta))
    except KeyError:
        await callback.answer()
        return
    text, kb = await render_gset_bank()
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:gsetinsurance")
async def cb_gset_insurance(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_gset_insurance()
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:gadji:"))
async def cb_gadj_insurance(callback: CallbackQuery) -> None:
    _, _, key, delta = callback.data.split(":")
    try:
        await global_settings.adjust(key, int(delta))
    except KeyError:
        await callback.answer()
        return
    text, kb = await render_gset_insurance()
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:gsetcorp")
async def cb_gset_corp(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, kb = await render_gset_corp()
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:gadjc:"))
async def cb_gadj_corp(callback: CallbackQuery) -> None:
    _, _, key, delta = callback.data.split(":")
    try:
        await global_settings.adjust(key, int(delta))
    except KeyError:
        await callback.answer()
        return
    text, kb = await render_gset_corp()
    await _edit(callback, text, kb)


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
    await _edit(callback, texts.ADMIN_ENTER_SIZE, _CANCEL_KB)


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
    await _edit(callback, texts.ADMIN_ENTER_NAME, _CANCEL_KB)


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


@router.callback_query(F.data.startswith("adm:setlabel:"))
async def cb_set_public_label(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, chat_id, user_id = callback.data.split(":")
    parsed_chat_id, parsed_user_id = int(chat_id), int(user_id)
    await state.set_state(AdminStates.set_public_label)
    await state.update_data(chat_id=parsed_chat_id, user_id=parsed_user_id)
    await _edit(
        callback,
        texts.ADMIN_ENTER_PUBLIC_LABEL,
        _public_label_cancel_kb(parsed_chat_id, parsed_user_id),
    )


@router.message(AdminStates.set_public_label)
async def msg_set_public_label(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    raw = message.text
    normalized = raw.strip() if raw is not None else ""
    invalid = (
        raw is None
        or "\r" in raw
        or "\n" in raw
        or not normalized
        or len(normalized) > texts.MAX_PUBLIC_LABEL_LEN
    )
    cancel_kb = _public_label_cancel_kb(data["chat_id"], data["user_id"])
    if invalid:
        await message.answer(
            f"{texts.ADMIN_PUBLIC_LABEL_INVALID}\n\n{texts.ADMIN_ENTER_PUBLIC_LABEL}",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    result = await admin_actions.set_public_label(
        message.from_user.id,
        data["user_id"],
        None if normalized == "-" else normalized,
    )
    if not result.ok:
        await message.answer(
            f"{result.message}\n\n{texts.ADMIN_ENTER_PUBLIC_LABEL}",
            reply_markup=cancel_kb,
            parse_mode="HTML",
        )
        return

    await state.clear()
    text, kb = await render_player(data["chat_id"], data["user_id"])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm:find")
async def cb_find(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.find_query)
    # Always offer a way out: an accidental tap on "find" must not strand the
    # admin in the text-input state with no Back/Cancel button.
    await _edit(callback, texts.ADMIN_ENTER_FIND, _CANCEL_KB)


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


MAX_BROADCAST_CHARS = 4096


def _bcast_lines(recipients: list[dict], counts: list[int]) -> list[str]:
    return [
        texts.bcast_recipient_label(item, count)
        for item, count in zip(recipients, counts, strict=True)
    ]


async def _bcast_draft(state: FSMContext) -> tuple[list[dict], str | None]:
    data = await state.get_data()
    return list(data.get("bcast_recipients") or []), data.get("bcast_text")


async def _bcast_counts(recipients: list[dict]) -> list[int]:
    active_days = (await get_config()).active_days
    return [await admin_actions.count_recipient(item, active_days) for item in recipients]


async def _bcast_total(recipients: list[dict]) -> int:
    active_days = (await get_config()).active_days
    return len(await admin_actions.resolve_broadcast_targets(recipients, active_days))


async def render_bcast_composer(state: FSMContext) -> tuple[str, InlineKeyboardMarkup]:
    """The letter: recipient list plus the text being written."""
    recipients, text = await _bcast_draft(state)
    lines = _bcast_lines(recipients, await _bcast_counts(recipients))
    total = await _bcast_total(recipients)
    rows = [
        [InlineKeyboardButton(text=texts.BTN_BCAST_ADD, callback_data="adm:bcr")],
        [
            InlineKeyboardButton(text=texts.BTN_BCAST_TEXT, callback_data="adm:bctext"),
            InlineKeyboardButton(text=texts.BTN_BCAST_PREVIEW, callback_data="adm:bcpre"),
        ],
        [InlineKeyboardButton(text=texts.BTN_BCAST_SEND, callback_data="adm:bcgo")],
        [InlineKeyboardButton(text=texts.BTN_HOME, callback_data="adm:home")],
    ]
    return (
        texts.admin_bcast_composer(lines, text, total),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def render_bcast_recipients(state: FSMContext) -> tuple[str, InlineKeyboardMarkup]:
    """Recipient builder: presets, an exact person or an exact chat, and removals."""
    recipients, _text = await _bcast_draft(state)
    counts = await _bcast_counts(recipients)
    lines = _bcast_lines(recipients, counts)
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"adm:bcadd:{kind}")]
        for kind, label in texts.BCAST_PRESET_LABELS.items()
    ]
    rows.append([InlineKeyboardButton(text=texts.BTN_BCAST_USER, callback_data="adm:bcadd:user")])
    rows.append([InlineKeyboardButton(text=texts.BTN_BCAST_CHAT, callback_data="adm:bcadd:chat")])
    rows.extend(
        [
            InlineKeyboardButton(text=f"❌ {line}", callback_data=f"adm:bcdel:{index}"),
        ]
        for index, line in enumerate(lines)
    )
    if recipients:
        rows.append([InlineKeyboardButton(text=texts.BTN_BCAST_CLEAR, callback_data="adm:bcclr")])
    rows.append([InlineKeyboardButton(text=texts.BTN_BCAST_BACK, callback_data="adm:bcast")])
    return (
        texts.admin_bcast_recipients_screen(lines, await _bcast_total(recipients)),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _add_recipient(state: FSMContext, item: dict) -> None:
    recipients, _text = await _bcast_draft(state)
    recipients.append(item)
    await state.update_data(bcast_recipients=recipients)
    await state.set_state(None)


@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    text, kb = await render_bcast_composer(state)
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:bcr")
async def cb_bcast_recipients(callback: CallbackQuery, state: FSMContext) -> None:
    text, kb = await render_bcast_recipients(state)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:bcadd:"))
async def cb_bcast_add(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":")[2]
    if kind == "user":
        await state.set_state(AdminStates.broadcast_user)
        await _edit(callback, texts.ADMIN_ENTER_BCAST_USER, _CANCEL_KB)
        return
    if kind == "chat":
        await state.set_state(AdminStates.broadcast_chat)
        await _edit(callback, texts.ADMIN_ENTER_BCAST_CHAT, _CANCEL_KB)
        return
    if kind not in admin_actions.PRESET_RECIPIENTS:
        await callback.answer()
        return
    await _add_recipient(state, {"kind": kind})
    text, kb = await render_bcast_recipients(state)
    await _edit(callback, text, kb)


@router.callback_query(F.data.startswith("adm:bcdel:"))
async def cb_bcast_del(callback: CallbackQuery, state: FSMContext) -> None:
    index = int(callback.data.split(":")[2])
    recipients, _text = await _bcast_draft(state)
    if 0 <= index < len(recipients):
        recipients.pop(index)
        await state.update_data(bcast_recipients=recipients)
    text, kb = await render_bcast_recipients(state)
    await _edit(callback, text, kb)


@router.callback_query(F.data == "adm:bcclr")
async def cb_bcast_clear(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(bcast_recipients=[])
    text, kb = await render_bcast_recipients(state)
    await _edit(callback, text, kb)


@router.message(AdminStates.broadcast_user)
async def msg_bcast_user(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    target = await admin_actions.find_user_target(raw)
    if target is None:
        await message.answer(texts.admin_bcast_lookup_failed(raw))
        return
    user_id, name = target
    await _add_recipient(state, {"kind": "user", "id": user_id, "label": name})
    text, kb = await render_bcast_recipients(state)
    await message.answer(text, reply_markup=kb)


@router.message(AdminStates.broadcast_chat)
async def msg_bcast_chat(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    target = await admin_actions.find_chat_target(raw)
    if target is None:
        await message.answer(texts.admin_bcast_lookup_failed(raw))
        return
    chat_id, title = target
    await _add_recipient(state, {"kind": "chat", "id": chat_id, "label": title})
    text, kb = await render_bcast_recipients(state)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "adm:bctext")
async def cb_bcast_text(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast_text)
    await _edit(callback, texts.ADMIN_ENTER_BCAST, _CANCEL_KB)


@router.message(AdminStates.broadcast_text)
async def msg_bcast(message: Message, state: FSMContext) -> None:
    text = message.html_text if message.text else None
    if not text:
        await state.clear()
        await message.answer(texts.ADMIN_BCAST_EMPTY, reply_markup=main_menu_kb())
        return
    if len(text) > MAX_BROADCAST_CHARS:
        await message.answer(texts.admin_bcast_too_long(MAX_BROADCAST_CHARS))
        return
    await state.update_data(bcast_text=text)
    await state.set_state(None)
    rendered, kb = await render_bcast_composer(state)
    await message.answer(rendered, reply_markup=kb)


@router.callback_query(F.data == "adm:bcpre")
async def cb_bcast_preview(callback: CallbackQuery, state: FSMContext) -> None:
    recipients, text = await _bcast_draft(state)
    if not text:
        await callback.answer(texts.admin_bcast_no_text(), show_alert=True)
        return
    if not recipients:
        await callback.answer(texts.admin_bcast_no_targets(), show_alert=True)
        return
    lines = _bcast_lines(recipients, await _bcast_counts(recipients))
    rows = [
        [InlineKeyboardButton(text=texts.BTN_BCAST_SEND, callback_data="adm:bcgo")],
        [InlineKeyboardButton(text=texts.BTN_BCAST_BACK, callback_data="adm:bcast")],
    ]
    await _edit(
        callback,
        texts.admin_bcast_target_text(text, lines, await _bcast_total(recipients)),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _bcast_send(bot: Bot, chat_id: int, text: str, thread_id: int | None) -> bool:
    """Send one broadcast message, honoring Telegram flood limits (RetryAfter).
    Returns True on success, False on any other failure."""
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML", message_thread_id=thread_id)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramAPIError:
            return False
    return False


async def _send_to_resolved_thread(
    bot: Bot, chat_id: int, text: str
) -> tuple[bool, str, int | None]:
    """Send to the configured/active topic, retrying deleted topics in General."""
    thread_id, reason = await threads_repo.resolve_thread(chat_id)
    ok = await _bcast_send(bot, chat_id, text, thread_id)
    if not ok and thread_id is not None:
        ok = await _bcast_send(bot, chat_id, text, None)
    return ok, reason, thread_id


@router.callback_query(F.data == "adm:bcgo")
async def cb_do_bcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    text = data.get("bcast_text")
    recipients = list(data.get("bcast_recipients") or [])
    if not text:
        await _edit(callback, texts.ADMIN_BCAST_LOST, main_menu_kb())
        return
    cfg = await get_config()
    targets = await admin_actions.resolve_broadcast_targets(recipients, cfg.active_days)
    if not targets:
        await _edit(callback, texts.ADMIN_BCAST_LOST, main_menu_kb())
        return
    if callback.message is not None:
        await callback.message.edit_text(texts.ADMIN_BCAST_STARTED)
    await callback.answer()
    # Pause between sends to stay under Telegram's ~30 msg/s flood cap.
    rate_delay = cfg.bcast_rate_delay
    sent = 0
    failed = 0
    for target in targets:
        if target.is_dm:
            ok = await _bcast_send(bot, target.chat_id, text, None)
            reason: str | None = "dm"
            thread_id = None
        else:
            ok, reason, thread_id = await _send_to_resolved_thread(bot, target.chat_id, text)
        sent += int(ok)
        failed += int(not ok)
        if ok and reason == "auto":
            await _bcast_send(bot, target.chat_id, texts.ADMIN_BCAST_AUTO_TOPIC, thread_id)
        await asyncio.sleep(rate_delay)
    summary = "+".join(str(item.get("kind", "")) for item in recipients)[:32] or "custom"
    await admin_actions.log_broadcast(
        callback.from_user.id, text[: texts.MAX_BCAST_PREVIEW_LEN], summary, sent, failed
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
