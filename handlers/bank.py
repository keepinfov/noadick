"""Inline-button bank panel: deposits, loans and the Corporation screen.

Entry points are the ``/bank`` (alias ``/банк``) and ``/corp`` (``/корп``)
commands; everything else is driven by ``bk:*`` callbacks. The panel is per-chat
and group-only — the deposit/loan accounts operate on the player's per-chat
``size``. Each panel is owned by the user who opened it: the opener's id is baked
into every callback, so a second user pressing the buttons is rejected.

Money operations live in ``services.bank`` and take the per-chat lock themselves,
so the handlers here only translate button presses into amounts and re-render.
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import texts
from callbacks import BankCallback
from services import bank
from services.settings import get_effective

router = Router()
logger = logging.getLogger(__name__)


class BankStates(StatesGroup):
    amount = State()


def _bank_callback(action: str, uid: int, value: str = "_") -> str:
    return BankCallback(action=action, user_id=uid, value=value).pack()


# --------------------------------------------------------------------------- #
# Keyboards.
# --------------------------------------------------------------------------- #


async def _main_kb(uid: int) -> InlineKeyboardMarkup:
    from repositories import bank as bank_repo

    corp = await bank_repo.get_rules_corp()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=texts.BTN_BANK_DEPOSIT, callback_data=_bank_callback("dep", uid)
            ),
            InlineKeyboardButton(
                text=texts.BTN_BANK_LOAN, callback_data=_bank_callback("loan", uid)
            ),
        ],
        [InlineKeyboardButton(text=texts.BTN_BANK_CORP, callback_data=_bank_callback("corp", uid))],
    ]
    if corp.rules_url_rude:
        rows.append([InlineKeyboardButton(text=texts.BTN_RULES_RUDE, url=corp.rules_url_rude)])
    if corp.rules_url_strict:
        rows.append([InlineKeyboardButton(text=texts.BTN_RULES_STRICT, url=corp.rules_url_strict)])
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.BTN_BANK_REFRESH, callback_data=_bank_callback("home", uid)
            ),
            InlineKeyboardButton(
                text=texts.BTN_BANK_CLOSE, callback_data=_bank_callback("close", uid)
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _dep_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Положить 25%", callback_data=_bank_callback("dopen", uid, "25")
                ),
                InlineKeyboardButton(
                    text="Положить 50%", callback_data=_bank_callback("dopen", uid, "50")
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Положить всё", callback_data=_bank_callback("dopen", uid, "all")
                ),
                InlineKeyboardButton(
                    text="Положить сумму", callback_data=_bank_callback("dopenc", uid)
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Снять 50%", callback_data=_bank_callback("dwd", uid, "50")
                ),
                InlineKeyboardButton(
                    text="Снять всё", callback_data=_bank_callback("dwd", uid, "all")
                ),
            ],
            [
                InlineKeyboardButton(text="Снять сумму", callback_data=_bank_callback("dwdc", uid)),
            ],
            [
                InlineKeyboardButton(
                    text="СЕКАСКО +5", callback_data=_bank_callback("sins", uid, "5")
                ),
                InlineKeyboardButton(
                    text="СЕКАСКО +10", callback_data=_bank_callback("sins", uid, "10")
                ),
            ],
            [
                InlineKeyboardButton(
                    text="СЕКАСКО максимум", callback_data=_bank_callback("sins", uid, "all")
                ),
                InlineKeyboardButton(
                    text="СЕКАСКО сумма", callback_data=_bank_callback("sinsc", uid)
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_BANK_BACK, callback_data=_bank_callback("home", uid)
                )
            ],
        ]
    )


def _loan_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять 50%", callback_data=_bank_callback("ltake", uid, "50")
                ),
                InlineKeyboardButton(
                    text="Взять максимум", callback_data=_bank_callback("ltake", uid, "all")
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Взять сумму", callback_data=_bank_callback("ltakec", uid)
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Погасить 50%", callback_data=_bank_callback("lrepay", uid, "50")
                ),
                InlineKeyboardButton(
                    text="Погасить всё", callback_data=_bank_callback("lrepay", uid, "all")
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Погасить сумму", callback_data=_bank_callback("lrepayc", uid)
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_BANK_BACK, callback_data=_bank_callback("home", uid)
                )
            ],
        ]
    )


def _corp_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_BANK_BACK, callback_data=_bank_callback("home", uid)
                )
            ]
        ]
    )


def _cancel_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.BTN_CANCEL, callback_data=_bank_callback("home", uid))]
        ]
    )


# --------------------------------------------------------------------------- #
# Commands.
# --------------------------------------------------------------------------- #


@router.message(Command("bank", "банк"))
async def cmd_bank(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer(texts.BANK_GROUP_ONLY)
        return
    eff = await get_effective(message.chat.id)
    if not eff.banking_enabled:
        await message.answer(texts.BANK_DISABLED)
        return
    summary = await bank.get_summary(message.chat.id, user.id)
    await message.answer(
        texts.bank_screen(summary), reply_markup=await _main_kb(user.id), parse_mode="HTML"
    )


@router.message(Command("corp", "корп"))
async def cmd_corp(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer(texts.BANK_GROUP_ONLY)
        return
    corp = await bank.corp_state(message.chat.id)
    await message.answer(texts.corp_screen(corp), reply_markup=_corp_kb(user.id), parse_mode="HTML")


# --------------------------------------------------------------------------- #
# Callback helpers.
# --------------------------------------------------------------------------- #


def _owns(callback: CallbackQuery, uid: int) -> bool:
    return callback.from_user is not None and callback.from_user.id == uid


def _callback_args(callback: CallbackQuery, callback_data: BankCallback | None) -> tuple[int, str]:
    if callback_data is not None:
        return callback_data.user_id, callback_data.value
    parts = (callback.data or "").split(":")
    return int(parts[2]), parts[3] if len(parts) > 3 else "_"


async def _invalid_callback(callback: CallbackQuery) -> None:
    await callback.answer(texts.CALLBACK_INVALID, show_alert=True)


def _plain(text: str) -> str:
    """Callback alerts are plain text — strip the HTML tags our notices carry so
    they don't show up literally as ``<b>...</b>`` in the toast."""
    return re.sub(r"<[^>]+>", "", text)


async def _edit(callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def _show_main(callback: CallbackQuery, chat_id: int, uid: int) -> None:
    summary = await bank.get_summary(chat_id, uid)
    await _edit(callback, texts.bank_screen(summary), await _main_kb(uid))


# --------------------------------------------------------------------------- #
# Navigation callbacks.
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("bk:noop:"))
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(BankCallback.filter(F.action == "home"))
@router.callback_query(F.data.startswith("bk:home:"))
async def cb_home(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    try:
        uid, _ = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    await state.clear()
    await _show_main(callback, callback.message.chat.id, uid)
    await callback.answer()


@router.callback_query(BankCallback.filter(F.action == "close"))
@router.callback_query(F.data.startswith("bk:close:"))
async def cb_close(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    try:
        uid, _ = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    await state.clear()
    if callback.message is not None:
        try:
            await callback.message.delete()
        except TelegramAPIError:
            logger.info("Could not delete bank panel")
    await callback.answer()


@router.callback_query(BankCallback.filter(F.action == "dep"))
@router.callback_query(F.data.startswith("bk:dep:"))
async def cb_dep(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, _ = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    summary = await bank.get_summary(callback.message.chat.id, uid)
    await _edit(callback, texts.bank_dep_screen(summary), _dep_kb(uid))
    await callback.answer()


@router.callback_query(BankCallback.filter(F.action == "loan"))
@router.callback_query(F.data.startswith("bk:loan:"))
async def cb_loan(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, _ = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    summary = await bank.get_summary(callback.message.chat.id, uid)
    await _edit(callback, texts.bank_loan_screen(summary), _loan_kb(uid))
    await callback.answer()


@router.callback_query(BankCallback.filter(F.action == "corp"))
@router.callback_query(F.data.startswith("bk:corp:"))
async def cb_corp(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, _ = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    corp = await bank.corp_state(callback.message.chat.id)
    await _edit(callback, texts.corp_screen(corp), _corp_kb(uid))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Money operations (preset amounts).
# --------------------------------------------------------------------------- #


def _pct_amount(total: int, arg: str) -> int | None:
    """Resolve a preset button: ``all`` -> None (handler-specific full amount),
    otherwise a percentage of ``total`` (min 1)."""
    if arg == "all":
        return None
    if not arg.isdigit() or not 1 <= int(arg) <= 100:
        raise ValueError("invalid percentage preset")
    return max(1, total * int(arg) // 100)


async def _preset_amount(callback: CallbackQuery, total: int, arg: str) -> int | None:
    try:
        return _pct_amount(total, arg)
    except ValueError:
        await _invalid_callback(callback)
        raise


async def _run_op(callback: CallbackQuery, uid: int, notice: str, back_to: str) -> None:
    """After a successful op, alert the user and re-render the relevant screen."""
    await callback.answer(_plain(notice), show_alert=True)
    chat_id = callback.message.chat.id
    summary = await bank.get_summary(chat_id, uid)
    if back_to == "dep":
        await _edit(callback, texts.bank_dep_screen(summary), _dep_kb(uid))
    elif back_to == "loan":
        await _edit(callback, texts.bank_loan_screen(summary), _loan_kb(uid))
    else:
        await _edit(callback, texts.bank_screen(summary), await _main_kb(uid))


@router.callback_query(BankCallback.filter(F.action == "dopen"))
@router.callback_query(F.data.startswith("bk:dopen:"))
async def cb_dep_open(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, arg = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    chat_id = callback.message.chat.id
    summary = await bank.get_summary(chat_id, uid)
    try:
        amount = summary.size if arg == "all" else await _preset_amount(callback, summary.size, arg)
    except ValueError:
        return
    if not amount or amount < 1:
        await callback.answer(texts.BANK_ERR["no_size"], show_alert=True)
        return
    try:
        res = await bank.open_deposit(chat_id, uid, amount)
    except bank.BankError as e:
        await callback.answer(
            texts.BANK_ERR.get(e.code, texts.BANK_ERR["bad_amount"]), show_alert=True
        )
        return
    await _run_op(callback, uid, texts.dep_opened(res.amount), "dep")


@router.callback_query(BankCallback.filter(F.action == "dwd"))
@router.callback_query(F.data.startswith("bk:dwd:"))
async def cb_dep_withdraw(
    callback: CallbackQuery, callback_data: BankCallback | None = None
) -> None:
    try:
        uid, arg = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    chat_id = callback.message.chat.id
    summary = await bank.get_summary(chat_id, uid)
    if summary.deposit is None:
        await callback.answer(texts.BANK_ERR["no_deposit"], show_alert=True)
        return
    try:
        amount = (
            None if arg == "all" else await _preset_amount(callback, summary.deposit.principal, arg)
        )
    except ValueError:
        return
    try:
        res = await bank.withdraw_deposit(chat_id, uid, amount)
    except bank.BankError as e:
        await callback.answer(
            texts.BANK_ERR.get(e.code, texts.BANK_ERR["bad_amount"]), show_alert=True
        )
        return
    await _run_op(callback, uid, texts.dep_withdrawn(res.amount, res.extra), "dep")


@router.callback_query(BankCallback.filter(F.action == "sins"))
@router.callback_query(F.data.startswith("bk:sins:"))
async def cb_sekasko(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, arg = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    chat_id = callback.message.chat.id
    summary = await bank.get_summary(chat_id, uid)
    if summary.deposit is None:
        await callback.answer(texts.BANK_ERR["no_deposit"], show_alert=True)
        return
    from services.global_settings import get_config_sync

    available = max(
        0,
        min(summary.deposit.principal, get_config_sync().sekasko_max_coverage)
        - summary.deposit.insured,
    )
    amount = available if arg == "all" else int(arg) if arg.isdigit() else 0
    try:
        res = await bank.buy_sekasko(chat_id, uid, amount)
    except bank.BankError as e:
        await callback.answer(
            texts.BANK_ERR.get(e.code, texts.BANK_ERR["bad_amount"]), show_alert=True
        )
        return
    await _run_op(callback, uid, texts.sekasko_bought(res.amount, res.extra), "dep")


@router.callback_query(BankCallback.filter(F.action == "ltake"))
@router.callback_query(F.data.startswith("bk:ltake:"))
async def cb_loan_take(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, arg = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    chat_id = callback.message.chat.id
    summary = await bank.get_summary(chat_id, uid)
    try:
        amount = (
            summary.loan_limit
            if arg == "all"
            else await _preset_amount(callback, summary.loan_limit, arg)
        )
    except ValueError:
        return
    # When the limit is 0 we still attempt with 1 so the service tells us the
    # precise reason (bad credit vs. an empty Corporation till).
    amount = max(1, amount or 0)
    try:
        res = await bank.take_loan(chat_id, uid, amount)
        loan = (await bank.get_summary(chat_id, uid)).loan
    except bank.BankError as e:
        await callback.answer(
            texts.BANK_ERR.get(e.code, texts.BANK_ERR["bad_amount"]), show_alert=True
        )
        return
    due = loan.due_at if loan else 0
    await _run_op(callback, uid, texts.loan_taken(res.amount, due), "loan")


@router.callback_query(BankCallback.filter(F.action == "lrepay"))
@router.callback_query(F.data.startswith("bk:lrepay:"))
async def cb_loan_repay(callback: CallbackQuery, callback_data: BankCallback | None = None) -> None:
    try:
        uid, arg = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    chat_id = callback.message.chat.id
    summary = await bank.get_summary(chat_id, uid)
    if summary.loan is None:
        await callback.answer(texts.BANK_ERR["no_loan"], show_alert=True)
        return
    try:
        amount = None if arg == "all" else await _preset_amount(callback, summary.loan.debt, arg)
    except ValueError:
        return
    try:
        res = await bank.repay_loan(chat_id, uid, amount)
        cleared = (await bank.get_summary(chat_id, uid)).loan is None
    except bank.BankError as e:
        await callback.answer(
            texts.BANK_ERR.get(e.code, texts.BANK_ERR["bad_amount"]), show_alert=True
        )
        return
    await _run_op(callback, uid, texts.loan_repaid(res.amount, cleared), "loan")


# --------------------------------------------------------------------------- #
# Custom-amount FSM.
# --------------------------------------------------------------------------- #

_ACTION_LABEL = {
    "dopenc": "вклад",
    "dwdc": "снятие",
    "ltakec": "кредит",
    "lrepayc": "погашение",
    "sinsc": "СЕКАСКО",
}


async def _prompt_amount(
    callback: CallbackQuery,
    state: FSMContext,
    action: str,
    callback_data: BankCallback | None,
) -> None:
    try:
        uid, _ = _callback_args(callback, callback_data)
    except (IndexError, ValueError):
        await _invalid_callback(callback)
        return
    if not _owns(callback, uid):
        await callback.answer(texts.BANK_NOT_YOURS, show_alert=True)
        return
    await state.set_state(BankStates.amount)
    await state.update_data(action=action, uid=uid, chat_id=callback.message.chat.id)
    await _edit(callback, texts.bank_enter_amount(_ACTION_LABEL[action]), _cancel_kb(uid))
    await callback.answer()


@router.callback_query(BankCallback.filter(F.action == "dopenc"))
@router.callback_query(F.data.startswith("bk:dopenc:"))
async def cb_dopenc(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    await _prompt_amount(callback, state, "dopenc", callback_data)


@router.callback_query(BankCallback.filter(F.action == "dwdc"))
@router.callback_query(F.data.startswith("bk:dwdc:"))
async def cb_dwdc(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    await _prompt_amount(callback, state, "dwdc", callback_data)


@router.callback_query(BankCallback.filter(F.action == "ltakec"))
@router.callback_query(F.data.startswith("bk:ltakec:"))
async def cb_ltakec(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    await _prompt_amount(callback, state, "ltakec", callback_data)


@router.callback_query(BankCallback.filter(F.action == "lrepayc"))
@router.callback_query(F.data.startswith("bk:lrepayc:"))
async def cb_lrepayc(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    await _prompt_amount(callback, state, "lrepayc", callback_data)


@router.callback_query(BankCallback.filter(F.action == "sinsc"))
@router.callback_query(F.data.startswith("bk:sinsc:"))
async def cb_sinsc(
    callback: CallbackQuery,
    state: FSMContext,
    callback_data: BankCallback | None = None,
) -> None:
    await _prompt_amount(callback, state, "sinsc", callback_data)


@router.message(BankStates.amount)
async def msg_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    action = data.get("action")
    uid = data.get("uid")
    chat_id = data.get("chat_id")
    if action not in _ACTION_LABEL or uid is None or chat_id is None:
        await state.clear()
        return
    if message.from_user is None or message.from_user.id != uid:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer(
            f"{texts.BANK_ERR['bad_amount']}\n\n{texts.bank_enter_amount(_ACTION_LABEL[action])}",
            reply_markup=_cancel_kb(uid),
        )
        return
    amount = int(raw)

    notice: str
    try:
        if action == "dopenc":
            res = await bank.open_deposit(chat_id, uid, amount)
            notice = texts.dep_opened(res.amount)
        elif action == "dwdc":
            res = await bank.withdraw_deposit(chat_id, uid, amount)
            notice = texts.dep_withdrawn(res.amount, res.extra)
        elif action == "ltakec":
            res = await bank.take_loan(chat_id, uid, amount)
            loan = (await bank.get_summary(chat_id, uid)).loan
            notice = texts.loan_taken(res.amount, loan.due_at if loan else 0)
        elif action == "lrepayc":
            res = await bank.repay_loan(chat_id, uid, amount)
            cleared = (await bank.get_summary(chat_id, uid)).loan is None
            notice = texts.loan_repaid(res.amount, cleared)
        elif action == "sinsc":
            res = await bank.buy_sekasko(chat_id, uid, amount)
            notice = texts.sekasko_bought(res.amount, res.extra)
        else:
            return
    except bank.BankError as e:
        await message.answer(
            f"{texts.BANK_ERR.get(e.code, texts.BANK_ERR['bad_amount'])}\n\n"
            f"{texts.bank_enter_amount(_ACTION_LABEL[action])}",
            reply_markup=_cancel_kb(uid),
        )
        return

    await state.clear()
    summary = await bank.get_summary(chat_id, uid)
    await message.answer(notice, parse_mode="HTML")
    await message.answer(
        texts.bank_screen(summary), reply_markup=await _main_kb(uid), parse_mode="HTML"
    )
