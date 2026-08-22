"""HTML-safe captions and keyboards for public weekly seasons."""

from __future__ import annotations

import html
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from callbacks import SeasonCallback
from services import seasons

_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_SUBTITLES = (
    "Неделя, когда калькулятор просил увольнение.",
    "Семь дней роста, падения и бухгалтерского унижения.",
    "Таблица всё запомнила. Даже то, что вы пытались забыть.",
    "Линейка составила протокол коллективного позора.",
)


def _name(value: str, limit: int = 24) -> str:
    clean = " ".join(value.split()) or "Безымянный"
    if len(clean) > limit:
        clean = clean[: limit - 1] + "…"
    return html.escape(clean)


def _signed(value: int) -> str:
    return f"{value:+d}"


def _period(report: seasons.SeasonReport) -> str:
    zone = ZoneInfo(report.timezone)
    start = datetime.fromtimestamp(report.starts_at, zone)
    end = datetime.fromtimestamp(report.ends_at - 1, zone)
    if start.month == end.month:
        return f"{start.day}–{end.day} {_MONTHS[end.month - 1]}"
    return f"{start.day} {_MONTHS[start.month - 1]} – {end.day} {_MONTHS[end.month - 1]}"


def _pct(delta: int, start: int) -> str:
    if start <= 0:
        return f"{_signed(delta)} см · —"
    return f"{_signed(delta)} см · {delta / start * 100:+.1f}%"


def _player_name(report: seasons.SeasonReport, user_id: int | None) -> str:
    if user_id is None:
        return "—"
    player = next((value for value in report.players if value.user_id == user_id), None)
    return _name(player.name) if player else html.escape(str(user_id))


def _prize_expiry(timestamp: int, timezone: str) -> str:
    value = datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
    return f"{value.day} {_MONTHS[value.month - 1]}, {value:%H:%M}"


def _heading(report: seasons.SeasonReport) -> list[str]:
    if report.is_partial:
        title = "🔥 <b>Разминка сезона · неполная неделя</b>"
        status = "Статистика идёт с момента установки и в официальный архив не попадёт."
    elif report.status == seasons.STATUS_LIVE:
        title = f"🍆 <b>Сезон #{report.season_number} · {_period(report)}</b>"
        status = "⏳ Предварительные итоги — неделя ещё не закрыта."
    else:
        title = f"🍆 <b>Сезон #{report.season_number} · {_period(report)}</b>"
        status = "✅ Итоги зафиксированы и больше не пересчитываются."
    subtitle = _SUBTITLES[report.season_number % len(_SUBTITLES)]
    return [title, f"<i>{subtitle}</i>", status]


def _bounded(lines: list[str]) -> str:
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line])
        if len(candidate) > 1024:
            break
        kept.append(line)
    return "\n".join(kept)


def _combined_players(report: seasons.SeasonReport) -> tuple[seasons.PlayerReport, ...]:
    dick_rank = {value.user_id: index for index, value in enumerate(report.dick_leaders)}
    wealth_rank = {value.user_id: index for index, value in enumerate(report.wealth_leaders)}
    missing = len(report.players) + 1
    return tuple(
        sorted(
            report.players,
            key=lambda value: (
                min(dick_rank.get(value.user_id, missing), wealth_rank.get(value.user_id, missing)),
                dick_rank.get(value.user_id, missing),
                wealth_rank.get(value.user_id, missing),
                value.user_id,
            ),
        )
    )


def caption(report: seasons.SeasonReport, *, full: bool = False, page: int = 0) -> str:
    lines = _heading(report)
    prizes = tuple(
        sorted(
            (value for value in report.players if value.sekasko_prize_rank > 0),
            key=lambda value: (value.sekasko_prize_rank, value.user_id),
        )
    )
    if report.is_empty:
        lines.extend(
            [
                "",
                "🏜 <b>Пустая неделя</b>",
                "Никто не нажал /dick. Даже случайность отказалась вас унижать.",
            ]
        )
    dick = report.dick_leaders[:5]
    wealth = report.wealth_leaders[:5]
    if full:
        combined = _combined_players(report)
        pages = max(1, (len(combined) + 9) // 10)
        safe_page = min(max(0, page), pages - 1)
        chunk = combined[safe_page * 10 : safe_page * 10 + 10]
        lines.extend(
            [
                "",
                f"📋 <b>Общая таблица · {safe_page + 1}/{pages}</b>",
                "<i>/dick: итог · среднее · дни | состояние</i>",
            ]
        )
        lines.extend(
            f"{safe_page * 10 + index}. {_name(row.name, 14)} — "
            f"<b>{_signed(row.dick_total)}</b> · {row.dick_average:+.1f} · "
            f"{row.active_days} | <b>{_signed(row.wealth_delta)}</b>"
            + (f" | 🛡 {row.sekasko_prize_amount} см" if row.sekasko_prize_amount > 0 else "")
            for index, row in enumerate(chunk, 1)
        )
        if not chunk:
            lines.append("— таблица пуста, как обещания после плохого броска")
        return _bounded(lines)
    else:
        dick_winner = dick[0] if dick else None
        wealth_winner = wealth[0] if wealth else None
        lines.extend(
            [
                "",
                "🏆 <b>Главный отросток</b>",
                (
                    f"{_name(dick_winner.name)} — <b>{_signed(dick_winner.dick_total)} см</b> · "
                    f"{dick_winner.dick_count} брос. · ø {dick_winner.dick_average:+.1f} · "
                    f"{dick_winner.active_days} дн."
                    if dick_winner
                    else "— претендентов нет"
                ),
                "",
                "💰 <b>Капиталист недели</b>",
                (
                    f"{_name(wealth_winner.name)} — <b>{_signed(wealth_winner.wealth_delta)} см</b> · "
                    f"итог {wealth_winner.end_wealth} · {wealth_winner.active_days} дн."
                    if wealth_winner
                    else "— капиталисты закончились раньше капитала"
                ),
            ]
        )
        if prizes:
            lines.extend(["", "🛡 <b>Призы СЕКАСКО</b>"])
            lines.extend(
                f"{row.sekasko_prize_rank}. {_name(row.name)} — "
                f"<b>{row.sekasko_prize_amount} см</b> до "
                f"{_prize_expiry(row.sekasko_prize_expires_at, report.timezone)}"
                for row in prizes[:5]
            )
            if len(prizes) > 5:
                lines.append(f"…и ещё {len(prizes) - 5}")
            if any(row.sekasko_prize_expires_at > int(time.time()) for row in prizes):
                lines.append(
                    "<i>Бесплатная защита тела вклада уже действует, даже если вклада пока нет.</i>"
                )
            else:
                lines.append("<i>Срок этих наград уже истёк; в архиве показаны выданные призы.</i>")

    active = max(
        report.players, key=lambda value: (value.active_days, -value.user_id), default=None
    )
    total_rolls = sum(value.dick_count for value in report.players)
    lines.extend(
        [
            "",
            f"📏 Изменение общей длины: <b>{_pct(report.length_delta, report.length_start)}</b>",
            f"💼 Состояние чата: <b>{_pct(report.wealth_delta, report.wealth_start)}</b>",
            f"🏢 Эмиссия Корпорации: <b>{report.emission} см</b>",
            f"🎲 Бросков: <b>{total_rolls}</b>",
            f"🚀 Лучший: <b>{_signed(report.best_dick_delta)} см</b> · "
            f"{_player_name(report, report.best_dick_user_id)}"
            if report.best_dick_delta is not None
            else "🚀 Лучший: —",
            f"🪚 Худший: <b>{_signed(report.worst_dick_delta)} см</b> · "
            f"{_player_name(report, report.worst_dick_user_id)}"
            if report.worst_dick_delta is not None
            else "🪚 Худший: —",
            (
                f"🗓 Самый упорный: {_name(active.name)} · <b>{active.active_days} дн.</b>"
                if active and active.active_days
                else "🗓 Самый упорный: —"
            ),
        ]
    )
    return _bounded(lines)


def keyboard(
    report: seasons.SeasonReport,
    *,
    chat_id: int,
    owner_id: int,
    full: bool = False,
    page: int = 0,
    has_older: bool = True,
    has_newer: bool = True,
) -> InlineKeyboardMarkup:
    number = report.season_number

    def button(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=label,
            callback_data=SeasonCallback(
                action=action, chat_id=chat_id, season_no=number, owner_id=owner_id
            ).pack(),
        )

    rows = [[button("📌 Кратко" if full else "📋 Таблица", "s" if full else "f0")]]
    if full:
        pages = max(1, (len(report.players) + 9) // 10)
        paging: list[InlineKeyboardButton] = []
        if page > 0:
            paging.append(button("◀ 10", f"f{page - 1}"))
        if page + 1 < pages:
            paging.append(button("10 ▶", f"f{page + 1}"))
        if paging:
            rows.append(paging)
    archive: list[InlineKeyboardButton] = []
    if has_older:
        archive.append(button("◀ Раньше", "p"))
    if has_newer:
        archive.append(button("Позже ▶", "n"))
    if archive:
        rows.append(archive)
    if report.status == seasons.STATUS_LIVE:
        rows.append([button("🔄 Обновить", "r")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
