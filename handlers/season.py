"""Public weekly season command and owner-bound panel callbacks."""

from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
)

from callbacks import SeasonCallback
from presentation import seasons as season_view
from services import season_chart, seasons

router = Router()


async def _render_message(message: Message, report: seasons.SeasonReport, owner_id: int) -> None:
    png = await season_chart.render_png(report)
    has_older, has_newer = await _navigation(report)
    await message.answer_photo(
        BufferedInputFile(png, filename=f"season-{report.season_number}.png"),
        caption=season_view.caption(report),
        reply_markup=season_view.keyboard(
            report,
            chat_id=message.chat.id,
            owner_id=owner_id,
            has_older=has_older,
            has_newer=has_newer,
        ),
        parse_mode="HTML",
    )


@router.message(Command("season"))
async def cmd_season(message: Message) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Сезоны живут в группах. В одиночку это не чемпионат, а диагноз.")
        return
    if message.from_user is None:
        return
    report = await seasons.ensure_live(message.chat.id)
    await _render_message(message, report, message.from_user.id)


async def _nearest_archived(
    chat_id: int, current: int, *, newer: bool
) -> seasons.SeasonReport | None:
    reports = await seasons.list_reports(chat_id, limit=1000)
    if newer:
        live = await seasons.get_live_report(chat_id)
        if live is not None and not live.is_partial and live.season_number > current:
            reports = (*reports, live)
    candidates = [
        report
        for report in reports
        if (report.season_number > current if newer else report.season_number < current)
    ]
    if not candidates:
        return None
    return (
        min(candidates, key=lambda report: report.season_number)
        if newer
        else max(candidates, key=lambda report: report.season_number)
    )


async def _navigation(report: seasons.SeasonReport) -> tuple[bool, bool]:
    archived = await seasons.list_reports(report.chat_id, limit=1000)
    has_older = any(value.season_number < report.season_number for value in archived)
    has_newer = any(value.season_number > report.season_number for value in archived)
    live = await seasons.get_live_report(report.chat_id)
    if live is not None and not live.is_partial and live.season_number > report.season_number:
        has_newer = True
    return has_older, has_newer


async def _selected(
    chat_id: int, season_no: int, *, refresh: bool = False
) -> seasons.SeasonReport | None:
    live = await seasons.get_live_report(chat_id)
    if live is not None and live.season_number == season_no:
        return await seasons.ensure_live(chat_id) if refresh else live
    return await seasons.get_report(chat_id, season_no)


@router.callback_query(SeasonCallback.filter())
async def season_callback(callback: CallbackQuery, callback_data: SeasonCallback) -> None:
    message = callback.message
    if (
        not isinstance(message, Message)
        or message.chat.type not in {"group", "supergroup"}
        or message.chat.id != callback_data.chat_id
    ):
        await callback.answer("Кнопка забрела не в тот чат. Верни её к /season.", show_alert=True)
        return
    if callback.from_user.id != callback_data.owner_id:
        await callback.answer("Панель чужая. Свою линейку вызывай через /season.", show_alert=True)
        return
    full_action = callback_data.action.startswith("f") and callback_data.action[1:].isdigit()
    if callback_data.action not in {"s", "p", "n", "r"} and not full_action:
        await callback.answer("Кнопка сгнила. Открой /season заново.", show_alert=True)
        return

    current = await _selected(callback_data.chat_id, callback_data.season_no)
    if current is None:
        await callback.answer("Этот сезон уже потерялся в бухгалтерском аду.", show_alert=True)
        return
    report = current
    full = full_action
    page = int(callback_data.action[1:]) if full_action else 0
    if callback_data.action in {"p", "n"}:
        report = await _nearest_archived(
            callback_data.chat_id,
            callback_data.season_no,
            newer=callback_data.action == "n",
        )
        if report is None:
            await callback.answer("Дальше архива нет. Копать можно только собственное дно.")
            return
    elif callback_data.action == "r":
        if current.status != seasons.STATUS_LIVE:
            await callback.answer(
                "Закрытый сезон не переписывается, как бы ни чесалось.", show_alert=True
            )
            return
        report = await _selected(callback_data.chat_id, callback_data.season_no, refresh=True)
        if report is None:
            await callback.answer("Живой сезон сбежал во время обновления.", show_alert=True)
            return

    pages = max(1, (len(report.players) + 9) // 10)
    if page >= pages:
        await callback.answer("Этой страницы нет. Там даже позор не прописан.", show_alert=True)
        return
    has_older, has_newer = await _navigation(report)
    caption = season_view.caption(report, full=full, page=page)
    keyboard = season_view.keyboard(
        report,
        chat_id=callback_data.chat_id,
        owner_id=callback_data.owner_id,
        full=full,
        page=page,
        has_older=has_older,
        has_newer=has_newer,
    )
    try:
        if report.season_number != current.season_number or callback_data.action == "r":
            png = await season_chart.render_png(report)
            await message.edit_media(
                InputMediaPhoto(
                    media=BufferedInputFile(png, filename=f"season-{report.season_number}.png"),
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
        else:
            await message.edit_caption(caption=caption, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await callback.answer("Обновлено" if callback_data.action == "r" else "")
