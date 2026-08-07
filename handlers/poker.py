from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)

import texts
from db.models import PokerHand, PokerSeat, PokerTable
from models import poker as engine
from repositories import players as players_repo
from repositories import poker as repo
from services import poker as game
from services.settings import get_effective

router = Router()
logger = logging.getLogger(__name__)


class PokerStates(StatesGroup):
    custom_config = State()
    custom_raise = State()
    top_up = State()


@dataclass
class CreateOrigin:
    user_id: int
    chat_id: int
    thread_id: int | None
    expires_at: int


_create_origins: dict[str, CreateOrigin] = {}
_drafts: dict[int, dict] = {}
_bot_username: str | None = None


async def _username(bot: Bot) -> str:
    global _bot_username
    if _bot_username is None:
        me = await bot.me()
        _bot_username = me.username or ""
    return _bot_username


def _join_link(username: str, table_id: str) -> str:
    return f"https://t.me/{username}?start=poker_{table_id}"


def _invite_link(username: str, token: str) -> str:
    return f"https://t.me/{username}?start=pokerinvite_{token}"


def _mode_label(table: PokerTable) -> str:
    return "💸 ДЕНЕЖНЫЙ" if table.mode == "money" else "🎓 УЧЕБНЫЙ"


def _access_label(mode: str) -> str:
    return {"open": "открытый", "approval": "по одобрению", "invite": "по приглашениям"}[mode]


def _draft_text(draft: dict) -> str:
    title = "Новый денежный стол" if draft["mode"] == "money" else "Учебный стол"
    preset = {
        "quick": "Быстрый",
        "normal": "Обычный",
        "deep": "Глубокий",
        "custom": "Свой",
    }[draft["preset"]]
    return (
        f"🍆 <b>{title}</b>\n\n"
        f"Структура: {preset}\n"
        f"Бай-ин: {draft['buy_in']} см\n"
        f"Блайнды: {draft['small_blind']} / {draft['big_blind']}\n"
        f"Мест: {draft['max_seats']}\n"
        f"Ход: {draft['timeout']} сек\n"
        f"Доступ: {_access_label(draft['access_mode'])}"
    )


def _draft_kb(draft: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎛 Структура", callback_data="pc:preset"),
                InlineKeyboardButton(text="✍️ Свои числа", callback_data="pc:custom"),
            ],
            [
                InlineKeyboardButton(
                    text=f"👥 Мест: {draft['max_seats']}", callback_data="pc:seats"
                ),
                InlineKeyboardButton(text=f"⏱ {draft['timeout']} сек", callback_data="pc:timeout"),
            ],
            [
                InlineKeyboardButton(
                    text=f"🔒 {_access_label(draft['access_mode'])}",
                    callback_data="pc:access",
                )
            ],
            [
                InlineKeyboardButton(text="✅ Открыть стол", callback_data="pc:publish"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="pc:cancel"),
            ],
        ]
    )


def _new_draft(
    user_id: int,
    *,
    mode: str,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> dict:
    buy_in = 40 if mode == "money" else 100
    preset = "normal" if mode == "money" else "deep"
    draft = {
        "user_id": user_id,
        "mode": mode,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "preset": preset,
        "buy_in": buy_in,
        "small_blind": 1,
        "big_blind": 2,
        "max_seats": 5,
        "timeout": 60,
        "access_mode": "approval",
    }
    _drafts[user_id] = draft
    return draft


def _poker_error(exc: Exception) -> str:
    return texts.poker_error(str(exc))


@router.message(Command("poker"))
async def cmd_poker(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return
    if message.chat.type == "private":
        active = await repo.get_active_seat(user.id)
        if active is not None:
            await refresh_table(bot, active.table_id)
            await message.answer(f"Ты уже за столом <code>#{active.table_id}</code>.")
            return
        draft = _new_draft(user.id, mode="practice")
        await message.answer(_draft_text(draft), reply_markup=_draft_kb(draft))
        return

    effective = await get_effective(message.chat.id)
    if not effective.poker_enabled:
        await message.answer(texts.POKER_DISABLED)
        return
    thread_id = message.message_thread_id if message.is_topic_message else None
    tables = await repo.list_location_tables(message.chat.id, thread_id)
    token = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
    _create_origins[token] = CreateOrigin(
        user_id=user.id,
        chat_id=message.chat.id,
        thread_id=thread_id,
        expires_at=int(time.time()) + 600,
    )
    username = await _username(bot)
    lines = ["🍆 <b>ПИСЮН-HOLDEM</b>"]
    if tables:
        lines.append("\nСтолы в этом месте:")
        for table in tables:
            count = await repo.count_active_seats(table.table_id)
            lines.append(
                f"• <code>#{table.table_id}</code> · {count}/{table.max_seats} · "
                f"{table.buy_in} см · {_access_label(table.access_mode)}"
            )
    else:
        lines.append("\nАктивных столов пока нет.")
    rows = []
    for table in tables:
        count = await repo.count_active_seats(table.table_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🚪 #{table.table_id} · {count}/{table.max_seats}",
                    url=_join_link(username, table.table_id),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Создать денежный стол",
                url=f"https://t.me/{username}?start=pokercreate_{token}",
            )
        ]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("\n".join(lines), reply_markup=kb)


@router.message(Command("start"), F.text.contains("poker"))
async def poker_start(message: Message, command: CommandObject, bot: Bot) -> None:
    user = message.from_user
    arg = command.args or ""
    if user is None:
        return
    if arg.startswith("pokercreate_"):
        token = arg.removeprefix("pokercreate_")
        origin = _create_origins.pop(token, None)
        if origin is None or origin.user_id != user.id or origin.expires_at < int(time.time()):
            await message.answer("Ссылка создания устарела. Снова напиши /poker в группе.")
            return
        effective = await get_effective(origin.chat_id)
        if not effective.poker_enabled:
            await message.answer(texts.POKER_DISABLED)
            return
        draft = _new_draft(
            user.id,
            mode="money",
            chat_id=origin.chat_id,
            thread_id=origin.thread_id,
        )
        await message.answer(_draft_text(draft), reply_markup=_draft_kb(draft))
        return
    if arg.startswith("pokerinvite_"):
        token = arg.removeprefix("pokerinvite_")
        try:
            table_id = await game.join_with_invite(token, user.id, user.first_name)
        except (repo.PokerRepoError, engine.PokerError) as exc:
            await message.answer(_poker_error(exc))
            return
        await message.answer(texts.POKER_REQUEST_APPROVED)
        await refresh_table(bot, table_id)
        return
    if not arg.startswith("poker_"):
        return
    table_id = arg.removeprefix("poker_").upper()
    try:
        outcome = await game.ask_to_join(table_id, user.id, user.first_name)
    except (repo.PokerRepoError, engine.PokerError) as exc:
        await message.answer(_poker_error(exc))
        return
    table = await repo.get_table(table_id)
    if table is None:
        await message.answer(texts.poker_error("table_missing"))
        return
    if outcome == "requested":
        await message.answer(texts.POKER_REQUEST_SENT)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Пустить", callback_data=f"pr:{table_id}:{user.id}:a"
                    ),
                    InlineKeyboardButton(
                        text="🚫 Отказать", callback_data=f"pr:{table_id}:{user.id}:d"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Пустить навсегда",
                        callback_data=f"pr:{table_id}:{user.id}:w",
                    ),
                    InlineKeyboardButton(
                        text="⛔ Запретить стол", callback_data=f"pr:{table_id}:{user.id}:b"
                    ),
                ],
            ]
        )
        try:
            await bot.send_message(
                table.host_id,
                f"🚪 <b>{html.escape(user.first_name)}</b> просится за стол "
                f"<code>#{table_id}</code>.\nБай-ин: {table.buy_in} см.",
                reply_markup=kb,
            )
        except TelegramAPIError:
            logger.info("Could not notify poker host about request")
        return
    await message.answer(texts.POKER_CREATED if outcome == "joined" else "Пульт обновлён.")
    await refresh_table(bot, table_id)


@router.inline_query()
async def poker_inline_invite(query: InlineQuery, bot: Bot) -> None:
    """Inline mode is intentionally invitation-only, never a game action."""
    raw = (query.query or "").strip().upper()
    table_id = raw.removeprefix("POKER ").removeprefix("#")
    table = await repo.get_table(table_id) if table_id else None
    if table is None or table.status == "closed":
        await query.answer([], cache_time=1, is_personal=True)
        return
    seats = await repo.list_seats(table_id)
    username = await _username(bot)
    result = InlineQueryResultArticle(
        id=f"poker-{table_id}",
        title=f"ПИСЮН-HOLDEM #{table_id}",
        description=f"{len(seats)}/{table.max_seats} игроков · бай-ин {table.buy_in} см",
        input_message_content=InputTextMessageContent(
            message_text=(
                f"🍆 <b>ПИСЮН-HOLDEM #{table_id}</b>\n"
                f"Бай-ин: {table.buy_in} см · блайнды {table.small_blind}/{table.big_blind}\n"
                "Хозяин уже разложил достоинство на столе. Твоё опоздание пока терпят."
            ),
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚪 Попроситься за стол",
                        url=_join_link(username, table_id),
                    )
                ]
            ]
        ),
    )
    await query.answer([result], cache_time=0, is_personal=True)


@router.callback_query(F.data.startswith("pc:"))
async def poker_create_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    draft = _drafts.get(callback.from_user.id)
    if draft is None:
        await callback.answer("Черновик устарел. Открой /poker заново.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    if action == "preset":
        order = ["quick", "normal", "deep"]
        current = draft["preset"] if draft["preset"] in order else "normal"
        preset = order[(order.index(current) + 1) % len(order)]
        buy_in, small, big = game.PRESETS[preset]
        if draft["mode"] == "practice" and preset == "normal":
            buy_in = 100
        draft.update(preset=preset, buy_in=buy_in, small_blind=small, big_blind=big)
    elif action == "seats":
        draft["max_seats"] = 2 if draft["max_seats"] >= 6 else draft["max_seats"] + 1
    elif action == "timeout":
        options = [30, 60, 90, 120, 180]
        draft["timeout"] = options[(options.index(draft["timeout"]) + 1) % len(options)]
    elif action == "access":
        modes = ["approval", "open", "invite"]
        draft["access_mode"] = modes[(modes.index(draft["access_mode"]) + 1) % len(modes)]
    elif action == "custom":
        await state.set_state(PokerStates.custom_config)
        await callback.message.edit_text(texts.POKER_CUSTOM_CONFIG)
        await callback.answer()
        return
    elif action == "cancel":
        _drafts.pop(callback.from_user.id, None)
        await state.clear()
        await callback.message.edit_text("Черновик стола выброшен.")
        await callback.answer()
        return
    elif action == "publish":
        try:
            table = await game.create_table(
                mode=draft["mode"],
                chat_id=draft["chat_id"],
                thread_id=draft["thread_id"],
                host_id=callback.from_user.id,
                host_name=callback.from_user.first_name,
                access_mode=draft["access_mode"],
                buy_in=draft["buy_in"],
                small_blind=draft["small_blind"],
                big_blind=draft["big_blind"],
                max_seats=draft["max_seats"],
                timeout=draft["timeout"],
            )
        except (repo.PokerRepoError, engine.PokerError) as exc:
            await callback.answer(_poker_error(exc), show_alert=True)
            return
        _drafts.pop(callback.from_user.id, None)
        await state.clear()
        await callback.message.edit_text(texts.POKER_CREATED)
        if table.mode == "money":
            try:
                text, kb = await render_public(bot, table.table_id)
                sent = await bot.send_message(
                    table.chat_id,
                    text,
                    reply_markup=kb,
                    message_thread_id=table.thread_id,
                )
                await repo.update_table(table.table_id, board_message_id=sent.message_id)
            except TelegramAPIError:
                await game.close(table.table_id, table.host_id, reason="publish_failed")
                await callback.message.answer(
                    "Не смог поставить табло в группу. Стол закрыт, бай-ин возвращён."
                )
                await callback.answer()
                return
        await refresh_table(bot, table.table_id)
        await callback.answer()
        return
    else:
        await callback.answer()
        return

    await callback.message.edit_text(_draft_text(draft), reply_markup=_draft_kb(draft))
    await callback.answer()


@router.message(PokerStates.custom_config)
async def poker_custom_config(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    draft = _drafts.get(user.id)
    if draft is None:
        await state.clear()
        return
    parts = (message.text or "").split()
    try:
        buy_in, small, big, seats, timeout = map(int, parts)
        game.validate_config(buy_in, small, big, seats, timeout)
    except (ValueError, repo.PokerRepoError):
        await message.answer(f"Не принял настройки.\n\n{texts.POKER_CUSTOM_CONFIG}")
        return
    draft.update(
        preset="custom",
        buy_in=buy_in,
        small_blind=small,
        big_blind=big,
        max_seats=seats,
        timeout=timeout,
    )
    await state.clear()
    await message.answer(_draft_text(draft), reply_markup=_draft_kb(draft))


@router.callback_query(F.data.startswith("pr:"))
async def poker_request_decision(callback: CallbackQuery, bot: Bot) -> None:
    try:
        _, table_id, user_raw, action = callback.data.split(":")
        user_id = int(user_raw)
        approve = action in {"a", "w"}
        remember = action in {"w", "b"}
        result = await game.decide_request(
            table_id,
            callback.from_user.id,
            user_id,
            approve=approve,
            remember=remember,
        )
    except (ValueError, repo.PokerRepoError, engine.PokerError) as exc:
        await callback.answer(_poker_error(exc), show_alert=True)
        return
    try:
        username = await _username(bot)
        user_kb = None
        if result == "approved":
            user_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Подтвердить вход и внести бай-ин",
                            url=_join_link(username, table_id),
                        )
                    ]
                ]
            )
        await bot.send_message(
            user_id,
            texts.POKER_REQUEST_APPROVED if result == "approved" else texts.POKER_REQUEST_DENIED,
            reply_markup=user_kb,
        )
    except TelegramAPIError:
        pass
    await callback.message.edit_text(
        "✅ Игрок допущен. У него две минуты подтвердить бай-ин."
        if result == "approved"
        else "🚫 Заявка отклонена."
    )
    await callback.answer()
    await refresh_table(bot, table_id)


def _table_state_lines(
    table: PokerTable, seats: list[PokerSeat], hand: PokerHand | None
) -> list[str]:
    lines = [f"🍆 <b>ПИСЮН-HOLDEM · #{table.table_id}</b>", _mode_label(table)]
    if table.mode == "practice":
        lines.append("Сантиметры резиновые и после закрытия исчезнут.")
    lines.extend(
        [
            f"Блайнды: {table.small_blind}/{table.big_blind} · бай-ин: {table.buy_in} см",
            f"Доступ: {_access_label(table.access_mode)} · мест: {len([s for s in seats if s.status == 'active'])}/{table.max_seats}",
        ]
    )
    state = hand.state if hand is not None else None
    if table.status == "playing" and state:
        street = {
            "preflop": "префлоп",
            "flop": "флоп",
            "turn": "тёрн",
            "river": "ривер",
        }[state["street"]]
        board = engine.cards_text(state["board"]) or "🂠 🂠 🂠 🂠 🂠"
        pot = sum(int(p["total_bet"]) for p in state["players"].values())
        lines.extend(
            [
                "",
                f"<b>Раздача №{table.hand_no} · {street}</b>",
                f"Стол: {board}",
                f"Банк: {pot} см · текущая ставка: {state['current_bet']} см",
            ]
        )
        current = state.get("current_uid")
        if current:
            name = html.escape(state["players"][current]["name"])
            deadline = time.strftime("%H:%M:%S", time.localtime(int(hand.deadline or 0)))
            lines.append(f"👉 Ходит <b>{name}</b> · решить до {deadline}")
    elif table.status == "closed":
        lines.extend(["", texts.POKER_CLOSED])
    else:
        label = "Лобби" if table.status == "lobby" else "Между раздачами"
        lines.extend(["", f"<b>{label}</b>"])

    lines.append("")
    for seat in seats:
        if seat.status != "active":
            continue
        stack = seat.stack
        marker = "👑" if seat.user_id == table.host_id else "•"
        status = ""
        if table.status != "playing":
            status = " · ✅ готов" if seat.ready else " · 💤 не готов"
        elif state and str(seat.user_id) not in state["players"]:
            status = " · сидит раздачу"
        elif state:
            player = state["players"][str(seat.user_id)]
            stack = int(player["stack"])
            if player["folded"]:
                status = " · фолд"
            elif player["all_in"]:
                status = " · ва-банк"
        if seat.pending_kick:
            status += " · вылетит после раздачи"
        elif seat.pending_leave:
            status += " · выйдет после раздачи"
        lines.append(f"{marker} {html.escape(seat.name)} — {stack} см{status}")
    if table.close_after_hand:
        lines.append("🛑 После этой раздачи хозяин прикроет шарагу и вернёт остатки.")
    if table.last_event:
        lines.extend(["", f"<i>{html.escape(table.last_event)}</i>"])
    return lines


def _showdown_lines(hand: PokerHand | None) -> list[str]:
    if hand is None or not hand.state.get("finished"):
        return []
    state = hand.state
    result = state.get("result") or {}
    lines = ["", "🏁 <b>ШОУДАУН</b>"]
    if result.get("showdown"):
        for uid, raw_score in result.get("scores", {}).items():
            player = state["players"][uid]
            score = tuple(raw_score)
            lines.append(
                f"{html.escape(player['name'])}: {engine.cards_text(player['cards'])} — "
                f"{engine.hand_name(score)}"
            )
    totals: dict[str, int] = {}
    for pot in result.get("pots", []):
        for uid, amount in pot.get("payouts", {}).items():
            totals[uid] = totals.get(uid, 0) + int(amount)
    for uid, amount in totals.items():
        lines.append(f"🍆 {html.escape(state['players'][uid]['name'])} получает {amount} см")
    rake = int(result.get("rake", 0))
    if rake:
        lines.append(f"🏢 Корпорация слизнула {rake} см комиссии.")
    return lines


async def render_public(bot: Bot, table_id: str) -> tuple[str, InlineKeyboardMarkup | None]:
    table = await repo.get_table(table_id)
    if table is None:
        raise repo.PokerRepoError("table_missing")
    seats = await repo.list_seats(table_id, active_only=table.status != "closed")
    hand = await repo.get_active_hand(table_id)
    if hand is None and table.hand_no:
        hand = await repo.get_latest_hand(table_id)
    lines = _table_state_lines(table, seats, hand)
    if table.status != "playing":
        lines += _showdown_lines(hand)
    kb = None
    if table.status != "closed":
        username = await _username(bot)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Войти / открыть пульт",
                        url=_join_link(username, table_id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📣 Позвать ещё органов",
                        switch_inline_query=f"poker {table_id}",
                    )
                ],
                [InlineKeyboardButton(text="📖 Правила", callback_data=f"pt:{table_id}:rules")],
            ]
        )
    return "\n".join(lines), kb


def _panel_text(
    table: PokerTable, seat: PokerSeat, seats: list[PokerSeat], hand: PokerHand | None
) -> str:
    lines = _table_state_lines(table, seats, hand)
    state = hand.state if hand is not None else None
    if table.status == "playing" and state and str(seat.user_id) in state["players"]:
        player = state["players"][str(seat.user_id)]
        lines.extend(
            [
                "",
                f"🎴 <b>Твоя рука: {engine.cards_text(player['cards'])}</b>",
                f"Ты внёс на улице: {player['street_bet']} см · всего: {player['total_bet']} см",
            ]
        )
        legal = engine.legal_actions(state, seat.user_id)
        if legal.get("turn"):
            lines.append(f"Для колла: {legal['to_call']} см. Сейчас твой ход.")
    elif table.status != "playing":
        lines += _showdown_lines(hand)
    return "\n".join(lines)


def _action_keyboard(table: PokerTable, seat: PokerSeat, hand: PokerHand | None):
    if table.status == "closed" or seat.status != "active":
        return None
    rows: list[list[InlineKeyboardButton]] = []
    if table.status == "playing" and hand is not None:
        state = hand.state
        if str(seat.user_id) in state["players"]:
            legal = engine.legal_actions(state, seat.user_id)
            if legal.get("turn"):
                version = hand.version
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="🗑 Сбросить", callback_data=f"pa:{table.table_id}:{version}:f"
                        )
                    ]
                )
                if legal["check"]:
                    rows[-1].append(
                        InlineKeyboardButton(
                            text="✅ Чек", callback_data=f"pa:{table.table_id}:{version}:k"
                        )
                    )
                else:
                    rows[-1].append(
                        InlineKeyboardButton(
                            text=f"✅ Колл {legal['call']}",
                            callback_data=f"pa:{table.table_id}:{version}:c",
                        )
                    )
                if legal["can_raise"]:
                    minimum = int(legal["min_raise_to"])
                    maximum = int(legal["max_raise_to"])
                    pot = int(legal["pot"])
                    targets = []
                    for label, target in (
                        ("Мин", minimum),
                        (
                            "½ банка",
                            int(state["current_bet"]) + max(int(state["min_raise"]), pot // 2),
                        ),
                        ("Банк", int(state["current_bet"]) + max(int(state["min_raise"]), pot)),
                    ):
                        target = max(minimum, min(maximum, target))
                        if target not in {value for _, value in targets}:
                            targets.append((label, target))
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"⬆️ {label} {target}",
                                callback_data=f"pa:{table.table_id}:{version}:r:{target}",
                            )
                            for label, target in targets
                        ]
                    )
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text="✍️ Своя сумма",
                                callback_data=f"pa:{table.table_id}:{version}:x",
                            ),
                            InlineKeyboardButton(
                                text=f"🍆 Ва-банк {maximum}",
                                callback_data=f"pa:{table.table_id}:{version}:r:{maximum}",
                            ),
                        ]
                    )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚪 Выйти после раздачи", callback_data=f"pt:{table.table_id}:leave"
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Готов" if not seat.ready else "💤 Не готов",
                    callback_data=f"pt:{table.table_id}:ready",
                ),
                InlineKeyboardButton(
                    text="➕ Докупить", callback_data=f"pt:{table.table_id}:topup"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚪 Забрать стек и выйти", callback_data=f"pt:{table.table_id}:leave"
                )
            ]
        )
        if seat.user_id == table.host_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="▶️ Начать с готовыми", callback_data=f"pt:{table.table_id}:start"
                    ),
                    InlineKeyboardButton(
                        text="⚙️ Управление", callback_data=f"pt:{table.table_id}:manage"
                    ),
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def refresh_table(bot: Bot, table_id: str) -> None:
    table = await repo.get_table(table_id)
    if table is None:
        return
    active_only = table.status != "closed"
    seats = await repo.list_seats(table_id, active_only=active_only)
    hand = await repo.get_active_hand(table_id)
    if hand is None and table.hand_no:
        hand = await repo.get_latest_hand(table_id)

    if table.mode == "money" and table.chat_id is not None:
        text, kb = await render_public(bot, table_id)
        if table.board_message_id is not None:
            try:
                await bot.edit_message_text(
                    text,
                    chat_id=table.chat_id,
                    message_id=table.board_message_id,
                    reply_markup=kb,
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    try:
                        sent = await bot.send_message(
                            table.chat_id,
                            text,
                            reply_markup=kb,
                            message_thread_id=table.thread_id,
                        )
                        await repo.update_table(table_id, board_message_id=sent.message_id)
                    except TelegramAPIError:
                        logger.info("Could not recreate poker board for %s", table_id)
            except TelegramAPIError:
                logger.info("Could not edit poker board for %s", table_id)

    for seat in seats:
        text = _panel_text(table, seat, seats, hand)
        kb = _action_keyboard(table, seat, hand)
        sent_id = seat.dm_message_id
        if sent_id is not None:
            try:
                await bot.edit_message_text(
                    text,
                    chat_id=seat.user_id,
                    message_id=sent_id,
                    reply_markup=kb,
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    sent_id = None
            except (TelegramForbiddenError, TelegramAPIError):
                sent_id = -1
        if sent_id is None:
            try:
                sent = await bot.send_message(seat.user_id, text, reply_markup=kb)
                await repo.set_dm_message(table_id, seat.user_id, sent.message_id)
            except TelegramAPIError:
                logger.info("Could not deliver poker panel to user")

        current_uid = None
        if table.status == "playing" and hand is not None:
            current_uid = hand.state.get("current_uid")
        if str(seat.user_id) == current_uid and hand is not None:
            if seat.turn_notice_version != hand.version:
                if seat.turn_notice_message_id:
                    try:
                        await bot.delete_message(seat.user_id, seat.turn_notice_message_id)
                    except TelegramAPIError:
                        pass
                try:
                    notice = await bot.send_message(seat.user_id, texts.POKER_TURN_NOTICE)
                    await repo.set_turn_notice(
                        table_id,
                        seat.user_id,
                        message_id=notice.message_id,
                        version=hand.version,
                    )
                except TelegramAPIError:
                    pass
        elif seat.turn_notice_message_id:
            try:
                await bot.delete_message(seat.user_id, seat.turn_notice_message_id)
            except TelegramAPIError:
                pass
            await repo.set_turn_notice(table_id, seat.user_id, message_id=None)


@router.callback_query(F.data.startswith("pa:"))
async def poker_action(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    try:
        parts = callback.data.split(":")
        _, table_id, version_raw, code, *rest = parts
        version = int(version_raw)
        action = {"f": "fold", "k": "check", "c": "call", "r": "raise"}.get(code)
        if code == "x":
            hand = await repo.get_active_hand(table_id)
            if hand is None or hand.version != version:
                raise repo.PokerRepoError("stale_action")
            legal = engine.legal_actions(hand.state, callback.from_user.id)
            if not legal.get("can_raise"):
                raise engine.PokerError("cannot_raise")
            await state.set_state(PokerStates.custom_raise)
            await state.update_data(
                poker_table=table_id,
                poker_version=version,
                poker_min=legal["min_raise_to"],
                poker_max=legal["max_raise_to"],
            )
            await callback.message.answer(
                f"{texts.POKER_CUSTOM_RAISE}\nМожно от {legal['min_raise_to']} до "
                f"{legal['max_raise_to']} см. Таймер продолжает тикать."
            )
            await callback.answer()
            return
        if action is None:
            raise engine.PokerError("unknown_action")
        amount = int(rest[0]) if rest else None
        await game.act(
            table_id,
            callback.from_user.id,
            action,
            version=version,
            amount=amount,
        )
    except (ValueError, repo.PokerRepoError, engine.PokerError) as exc:
        await callback.answer(_poker_error(exc), show_alert=True)
        try:
            table_id = callback.data.split(":")[1]
            await refresh_table(bot, table_id)
        except (IndexError, TelegramAPIError):
            pass
        return
    await callback.answer()
    await refresh_table(bot, table_id)


@router.message(PokerStates.custom_raise)
async def poker_custom_raise(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    try:
        amount = int((message.text or "").strip())
        if not int(data["poker_min"]) <= amount <= int(data["poker_max"]):
            raise ValueError
        await game.act(
            data["poker_table"],
            message.from_user.id,
            "raise",
            version=int(data["poker_version"]),
            amount=amount,
        )
    except (ValueError, KeyError):
        await message.answer(
            f"Нужно целое число от {data.get('poker_min', '?')} до {data.get('poker_max', '?')}."
        )
        return
    except (repo.PokerRepoError, engine.PokerError) as exc:
        await state.clear()
        await message.answer(_poker_error(exc))
        if data.get("poker_table"):
            await refresh_table(bot, data["poker_table"])
        return
    await state.clear()
    await refresh_table(bot, data["poker_table"])


@router.callback_query(F.data.startswith("pt:"))
async def poker_table_action(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    departure: str | None = None
    try:
        _, table_id, action = callback.data.split(":")
        if action == "ready":
            await game.toggle_ready(table_id, callback.from_user.id)
        elif action == "start":
            await game.start_with_ready(table_id, callback.from_user.id)
        elif action == "leave":
            hand = await repo.get_active_hand(table_id)
            if hand is not None and hand.state.get("current_uid") == str(callback.from_user.id):
                await game.act(
                    table_id,
                    callback.from_user.id,
                    "fold",
                    version=hand.version,
                )
            departure = await game.leave(table_id, callback.from_user.id)
        elif action == "topup":
            table = await repo.get_table(table_id)
            if table is None or table.status == "playing":
                raise repo.PokerRepoError("hand_active")
            await state.set_state(PokerStates.top_up)
            await state.update_data(poker_table=table_id)
            await callback.message.answer(texts.POKER_TOPUP_PROMPT)
            await callback.answer()
            return
        elif action == "manage":
            table = await repo.get_table(table_id)
            if table is None or table.host_id != callback.from_user.id:
                raise repo.PokerRepoError("host_only")
            text, kb = await render_management(table_id)
            await callback.message.answer(text, reply_markup=kb)
            await callback.answer()
            return
        elif action == "rules":
            await callback.answer(
                "No-limit Hold’em: две закрытые карты, пять общих, четыре круга ставок. "
                "Лучшая комбинация из любых пяти карт забирает банк.",
                show_alert=True,
            )
            return
        else:
            raise engine.PokerError("unknown_action")
    except (ValueError, repo.PokerRepoError, engine.PokerError) as exc:
        await callback.answer(_poker_error(exc), show_alert=True)
        return
    await callback.answer()
    if departure == "left" and callback.message is not None:
        try:
            await callback.message.edit_text(
                "🚪 Ты забрал остатки и отполз от стола. Карточный мир переживёт эту потерю."
            )
        except TelegramAPIError:
            pass
    await refresh_table(bot, table_id)


@router.message(PokerStates.top_up)
async def poker_topup_message(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    try:
        amount = int((message.text or "").strip())
        await game.top_up(data["poker_table"], message.from_user.id, amount)
    except ValueError:
        await message.answer(texts.poker_error("bad_amount"))
        return
    except (KeyError, repo.PokerRepoError) as exc:
        await state.clear()
        await message.answer(_poker_error(exc))
        return
    await state.clear()
    await refresh_table(bot, data["poker_table"])


async def render_management(table_id: str) -> tuple[str, InlineKeyboardMarkup | None]:
    table = await repo.get_table(table_id)
    if table is None:
        raise repo.PokerRepoError("table_missing")
    seats = await repo.list_seats(table_id)
    requests = await repo.list_requests(table_id)
    blocked = await repo.list_access_entries(table_id, "banned")
    lines = [
        f"⚙️ <b>Управление столом #{table_id}</b>",
        f"Статус: {table.status}",
        f"Доступ: {_access_label(table.access_mode)}",
        f"Мест: {len(seats)}/{table.max_seats}",
        f"Заявок: {len(requests)}",
        f"В чёрном списке: {len(blocked)}",
        f"Пауза: {'да' if table.paused else 'нет'}",
        "",
        "Игроки:",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for seat in seats:
        marker = "👑" if seat.user_id == table.host_id else "•"
        lines.append(f"{marker} {html.escape(seat.name)} — {seat.stack} см")
        if seat.user_id != table.host_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🚫 Убрать {seat.name[:20]}",
                        callback_data=f"ph:{table_id}:kick:{seat.user_id}",
                    )
                ]
            )
    if table.status == "closed":
        lines.extend(["", texts.POKER_CLOSED])
        return "\n".join(lines), None
    controls = [
        [
            InlineKeyboardButton(text="🔒 Сменить доступ", callback_data=f"ph:{table_id}:access"),
            InlineKeyboardButton(
                text=f"👥 Мест {table.max_seats}", callback_data=f"ph:{table_id}:seats"
            ),
        ],
        [
            InlineKeyboardButton(
                text="▶️ Снять паузу" if table.paused else "⏸ Пауза",
                callback_data=f"ph:{table_id}:pause",
            ),
            InlineKeyboardButton(text="🔗 Разовый пропуск", callback_data=f"ph:{table_id}:invite"),
        ],
        [InlineKeyboardButton(text="▶️ Начать с готовыми", callback_data=f"ph:{table_id}:start")],
        [InlineKeyboardButton(text="🛑 Закрыть стол", callback_data=f"ph:{table_id}:closeq")],
    ]
    if table.mode == "money":
        controls.insert(
            2,
            [
                InlineKeyboardButton(
                    text="👤 Позвать игрока чата", callback_data=f"ph:{table_id}:players:0"
                )
            ],
        )
    if blocked:
        controls.insert(
            -2,
            [
                InlineKeyboardButton(
                    text=f"📕 Чёрный список ({len(blocked)})",
                    callback_data=f"ph:{table_id}:blocked",
                )
            ],
        )
    rows.extend(controls)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("ph:"))
async def poker_host_action(callback: CallbackQuery, bot: Bot) -> None:
    try:
        parts = callback.data.split(":")
        _, table_id, action, *rest = parts
        table = await repo.get_table(table_id)
        if table is None or table.host_id != callback.from_user.id:
            raise repo.PokerRepoError("host_only")
        if action == "kick":
            await game.kick(table_id, callback.from_user.id, int(rest[0]))
        elif action == "access":
            modes = ["approval", "open", "invite"]
            mode = modes[(modes.index(table.access_mode) + 1) % len(modes)]
            await repo.update_table(table_id, access_mode=mode, host_active_at=int(time.time()))
        elif action == "seats":
            count = await repo.count_active_seats(table_id)
            candidate = 2 if table.max_seats >= 6 else table.max_seats + 1
            if candidate < count:
                candidate = count
            await repo.update_table(table_id, max_seats=candidate, host_active_at=int(time.time()))
        elif action == "pause":
            await repo.update_table(
                table_id, paused=not table.paused, host_active_at=int(time.time())
            )
        elif action == "invite":
            invite = await repo.create_invite(table_id)
            username = await _username(bot)
            link = _invite_link(username, invite.token)
            await callback.message.answer(
                f"Одноразовый пропуск на 24 часа:\n<code>{html.escape(link)}</code>",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📨 Переслать пропуск",
                                url=f"https://t.me/share/url?url={quote(link, safe='')}",
                            )
                        ]
                    ]
                ),
            )
        elif action == "players":
            if table.mode != "money" or table.chat_id is None:
                raise repo.PokerRepoError("not_a_player")
            offset = int(rest[0]) if rest else 0
            candidates = await players_repo.list_players_page(
                table.chat_id, offset=offset, limit=6, sort="s"
            )
            active_ids = {seat.user_id for seat in await repo.list_seats(table_id)}
            rows = [
                [
                    InlineKeyboardButton(
                        text=f"➕ {player.name[:24]} · {player.size} см",
                        callback_data=f"ph:{table_id}:allow:{player.user_id}",
                    )
                ]
                for player in candidates
                if player.user_id not in active_ids and not player.is_chat_banned
            ]
            nav: list[InlineKeyboardButton] = []
            if offset:
                nav.append(
                    InlineKeyboardButton(
                        text="←", callback_data=f"ph:{table_id}:players:{max(0, offset - 6)}"
                    )
                )
            if len(candidates) == 6:
                nav.append(
                    InlineKeyboardButton(
                        text="→", callback_data=f"ph:{table_id}:players:{offset + 6}"
                    )
                )
            if nav:
                rows.append(nav)
            rows.append([InlineKeyboardButton(text="Назад", callback_data=f"ph:{table_id}:back")])
            await callback.message.edit_text(
                "Кого заранее внести в белый список этого сомнительного заведения?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
            await callback.answer()
            return
        elif action == "allow":
            if table.mode != "money" or table.chat_id is None:
                raise repo.PokerRepoError("not_a_player")
            user_id = int(rest[0])
            player = await players_repo.get_player(table.chat_id, user_id)
            if player is None or player.is_chat_banned:
                raise repo.PokerRepoError("not_a_player")
            await repo.allow_user(table_id, user_id, player.name)
            username = await _username(bot)
            try:
                await bot.send_message(
                    user_id,
                    f"🍆 Тебя персонально зовут за стол <code>#{table_id}</code>. "
                    "Видимо, хозяину не хватало именно твоих неправильных решений.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Войти за стол",
                                    url=_join_link(username, table_id),
                                )
                            ]
                        ]
                    ),
                )
            except TelegramAPIError:
                await callback.message.answer(
                    f"Игрок добавлен в белый список, но личка у него наглухо заколочена. "
                    f"Передай ссылку сам: <code>{html.escape(_join_link(username, table_id))}</code>"
                )
        elif action == "blocked":
            blocked = await repo.list_access_entries(table_id, "banned")
            rows = [
                [
                    InlineKeyboardButton(
                        text=f"✅ Разрешить {entry.name[:22]}",
                        callback_data=f"ph:{table_id}:unblock:{entry.user_id}",
                    )
                ]
                for entry in blocked
            ]
            rows.append([InlineKeyboardButton(text="Назад", callback_data=f"ph:{table_id}:back")])
            await callback.message.edit_text(
                "📕 Этих деятелей хозяин пока не желает видеть даже проигрывающими:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
            await callback.answer()
            return
        elif action == "unblock":
            await repo.set_request_status(table_id, int(rest[0]), "denied")
        elif action == "start":
            await game.start_with_ready(table_id, callback.from_user.id)
        elif action == "closeq":
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Да, закрыть", callback_data=f"ph:{table_id}:close"
                        ),
                        InlineKeyboardButton(text="Нет", callback_data=f"ph:{table_id}:back"),
                    ]
                ]
            )
            await callback.message.edit_text(
                "Закрыть стол? Во время раздачи закрытие произойдёт после расчёта.",
                reply_markup=kb,
            )
            await callback.answer()
            return
        elif action == "close":
            await game.close(table_id, callback.from_user.id)
        elif action == "back":
            pass
        else:
            raise engine.PokerError("unknown_action")
        text, kb = await render_management(table_id)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    except (ValueError, repo.PokerRepoError, engine.PokerError) as exc:
        await callback.answer(_poker_error(exc), show_alert=True)
        return
    await callback.answer()
    await refresh_table(bot, table_id)


async def watchdog_loop(bot: Bot) -> None:
    """Recover turn timers and close abandoned rooms."""
    for table in await repo.list_active_tables():
        try:
            await refresh_table(bot, table.table_id)
        except Exception:
            logger.exception("Could not restore poker table %s", table.table_id)
    while True:
        try:
            changed = await game.process_due_actions()
            for table_id in changed:
                await refresh_table(bot, table_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Poker watchdog pass failed")
        await asyncio.sleep(1)
