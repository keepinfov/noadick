"""Compact, HTML-safe presentation for public game commands."""

from __future__ import annotations

import html
import random
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from services import analytics, bank

_recent: dict[str, deque[str]] = {}


def choose_line(key: str, lines: Sequence[str], **values: object) -> str:
    """Choose a contextual line without immediately repeating it.

    The tiny in-memory history is deliberately non-persistent: copy variety is
    cosmetic and must never become game state.
    """
    if not lines:
        return ""
    history = _recent.setdefault(key, deque(maxlen=min(2, max(1, len(lines) - 1))))
    candidates = [line for line in lines if line not in history] or list(lines)
    selected = random.choice(candidates)
    history.append(selected)
    escaped = {name: html.escape(str(value)) for name, value in values.items()}
    return selected.format(**escaped)


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def signed(value: int) -> str:
    return f"{value:+d}"


_DICK_LINES = {
    "catastrophe": (
        "Это уже не минус, а археологические раскопки достоинства.",
        "С таким броском сантиметры не потеряны — они сменили владельца и страну.",
        "Табло выжило. Самоуважение пока числится пропавшим.",
    ),
    "heavy_negative": (
        "Секатор хлопнул так громко, что таблица лидеров пригнулась.",
        "Внушительный шаг назад — почти разбег, только не в ту сторону.",
        "Сегодня генератор решил измерить глубину твоего финансового дна.",
    ),
    "small_negative": (
        "Мелочь, а трусы сидят заметно свободнее.",
        "Не катастрофа, просто сантиметры тихо уволились без заявления.",
        "Потеря небольшая, позор удобно помещается в карман.",
    ),
    "zero": (
        "Ноль. Генератор посмотрел на тебя и решил не тратить электричество.",
        "Ни роста, ни падения — даже случайность отказалась участвовать.",
        "Результат настолько пустой, что его можно сдавать в аренду.",
    ),
    "tiny_gain": (
        "Рост есть. Микроскоп тоже лучше пока не убирать.",
        "Плюс засчитан, фанфары попросили не беспокоить по пустякам.",
        "Сантиметры пришли пешком и явно из соседнего подъезда.",
    ),
    "gain": (
        "Уже похоже на рост, а не на погрешность линейки.",
        "Нормальный рабочий плюс — сегодня без цирка с секатором.",
        "Таблица заметила движение и нехотя подвинулась.",
    ),
    "big_gain": (
        "Жирный прирост. Остальным пора обвинять линейку в коррупции.",
        "Вот это уже заявка на то, чтобы испортить вечер всему топу.",
        "Генератор расщедрился — не привыкай, любимчиков у него нет.",
    ),
    "insurance": (
        "ПИСЯГО прикрыло задницу, но протокол позора уже подписан.",
        "Страховка сработала: достоинство бумажное, зато долг короче.",
        "ПИСЯГО поймало секатор зубами. На этот раз.",
    ),
    "debt": (
        "Резать нечего, поэтому Корпорация аккуратно оформила твой стыд в долг.",
        "Сантиметры виртуальные, долг совершенно настоящий. Финансовый гений.",
        "Секатор заменили договором: боль отложенная, проценты бодрые.",
    ),
    "clipped": (
        "Касса пустая: часть роста умерла между обещанием и выдачей.",
        "Корпорация показала большой плюс, затем выдала то, что нашла под диваном.",
        "Рост упёрся в кассовый потолок и размазался по бухгалтерии.",
    ),
}


@dataclass(frozen=True)
class DickView:
    user_id: int
    name: str
    game_delta: int
    size: int
    rank: int
    rank_before: int | None = None
    payout: bank.DickPayout | None = None
    pisyago: bank.PisyagoResult | None = None
    debt: int = 0
    debt_due: str = ""
    debt_defaulted: bool = False
    garnished: int = 0
    deposit_interest: int = 0
    disease: str = ""
    infection: str = ""


def dick_result(view: DickView) -> str:
    if view.pisyago and view.pisyago.covered:
        tier = "insurance"
    elif view.debt:
        tier = "debt"
    elif view.payout and view.payout.clipped:
        tier = "clipped"
    elif view.game_delta <= -50:
        tier = "catastrophe"
    elif view.game_delta <= -10:
        tier = "heavy_negative"
    elif view.game_delta < 0:
        tier = "small_negative"
    elif view.game_delta == 0:
        tier = "zero"
    elif view.game_delta <= 3:
        tier = "tiny_gain"
    elif view.game_delta <= 7:
        tier = "gain"
    else:
        tier = "big_gain"

    lines = [
        f"🍆 {mention(view.user_id, view.name)}: <b>{signed(view.game_delta)} см</b>",
        f"Размер: <b>{view.size} см</b> · место <b>#{view.rank}</b>",
    ]
    if view.rank_before and view.rank_before != view.rank:
        arrow = "↑" if view.rank < view.rank_before else "↓"
        lines[-1] += f" ({arrow}{abs(view.rank_before - view.rank)})"
    lines.extend(["", choose_line(f"dick:{tier}", _DICK_LINES[tier])])

    consequences: list[str] = []
    if view.payout and view.payout.clipped:
        consequences.append(
            f"🏦 Выдано {view.payout.credited}/{view.payout.nominal} см; "
            f"касса не нашла ещё {view.payout.clipped}."
        )
    elif view.payout and (view.payout.emitted or view.payout.corporation_paid):
        consequences.append(
            f"🏦 Рост профинансирован: эмиссия {view.payout.emitted} · "
            f"касса {view.payout.corporation_paid} см."
        )
    if view.pisyago and view.pisyago.covered:
        consequences.append(
            f"🛡 ПИСЯГО покрыло {view.pisyago.covered}/{view.pisyago.loss} см; "
            f"остаток покрытия {view.pisyago.remaining} см."
        )
    elif view.pisyago and view.pisyago.coverage_pct > 0 and view.pisyago.remaining == 0:
        reset = datetime.fromtimestamp(view.pisyago.reset_at).strftime("%d.%m.%Y %H:%M")
        consequences.append(f"🛡 ПИСЯГО исчерпано; восстановится {reset}.")
    if view.debt:
        due = f" до {html.escape(view.debt_due)}" if view.debt_due else ""
        status = " (уже просрочен)" if view.debt_defaulted else ""
        consequences.append(f"🧾 Новый долг: {view.debt} см{due}{status}.")
    if view.garnished:
        consequences.append(f"🩸 Взыскано с прироста: {view.garnished} см.")
    if view.deposit_interest:
        consequences.append(f"💰 Проценты по вкладу: +{view.deposit_interest} см.")
    if view.infection:
        consequences.append(f"🦠 {html.escape(view.infection)}")
    elif view.disease:
        consequences.append(html.escape(view.disease))
    if consequences:
        lines.extend(["", *consequences])
    return "\n".join(lines)


def dick_repeat(user_id: int, name: str, size: int, rank: int, remaining: str, disease: str) -> str:
    lines = [
        f"⏳ {mention(user_id, name)}, сегодня уже дёргал генератор.",
        f"Размер: <b>{size} см</b> · место <b>#{rank}</b>",
        f"Следующая попытка через <b>{html.escape(remaining)}</b>.",
    ]
    if disease:
        lines.append(html.escape(disease))
    return "\n".join(lines)


_DUEL_PUNCHES = {
    "upset": (
        "{winner} пришёл андердогом, а ушёл с чужими сантиметрами. Букмекер рыдает.",
        "{winner} сломал прогноз об колено и заодно укоротил {loser}.",
    ),
    "slow": (
        "{loser} принимал решение так долго, будто выбирал, каким боком проиграть.",
        "Пока {loser} думал, {winner} успел победить и сверить налоговую декларацию.",
    ),
    "blowout": (
        "{winner} устроил разгром. {loser} теперь статистическая погрешность.",
        "Это была не дуэль, а публичная утилизация {loser} силами {winner}.",
    ),
    "normal": (
        "{winner} забрал победу, а {loser} — ценный опыт быть расходником.",
        "{winner} оказался убедительнее. От {loser} требовалось хотя бы сопротивление.",
        "{winner} закончил спор сантиметрами; аргументы {loser} признаны короткими.",
    ),
}


@dataclass(frozen=True)
class DuelView:
    winner: str
    loser: str
    attacker: str
    defender: str
    attacker_before: int
    attacker_after: int
    defender_before: int
    defender_after: int
    stake: int
    profit: int
    tax: int
    base_chance: float
    final_chance: float
    winner_was_attacker: bool
    reaction_seconds: float
    garnished: int = 0
    disease_note: str = ""
    infection: str = ""
    attacker_tag: str = ""
    defender_tag: str = ""


def duel_result(view: DuelView) -> str:
    winner_pre_chance = view.base_chance if view.winner_was_attacker else 1 - view.base_chance
    swing = max(
        abs(view.attacker_after - view.attacker_before),
        abs(view.defender_after - view.defender_before),
    )
    if winner_pre_chance < 0.4:
        tier = "upset"
    elif view.reaction_seconds >= 20:
        tier = "slow"
    elif swing >= max(10, min(view.attacker_before, view.defender_before) // 2):
        tier = "blowout"
    else:
        tier = "normal"
    punch = choose_line(f"duel:{tier}", _DUEL_PUNCHES[tier], winner=view.winner, loser=view.loser)
    chance = view.final_chance if view.winner_was_attacker else 1 - view.final_chance
    lines = [
        f"⚔️ <b>{html.escape(view.winner)} победил {html.escape(view.loser)}</b>",
        punch,
        "",
        f"Ставка: {view.stake} см · победителю +{view.profit} · налог {view.tax}",
        f"{html.escape(view.attacker)}: {view.attacker_before} → <b>{view.attacker_after}</b> см{html.escape(view.attacker_tag)}",
        f"{html.escape(view.defender)}: {view.defender_before} → <b>{view.defender_after}</b> см{html.escape(view.defender_tag)}",
        f"Шанс победителя: {chance:.0%}",
    ]
    effects = []
    if view.garnished:
        effects.append(f"🩸 С выигрыша взыскано {view.garnished} см в долг.")
    if view.disease_note:
        effects.append(f"🦠 {html.escape(view.disease_note)}")
    if view.infection:
        effects.append(f"🦠 {html.escape(view.infection)}")
    if effects:
        lines.extend(["", *effects])
    return "\n".join(lines)


def profile(
    stats,
    *,
    user_id: int,
    bank_line: str | None = None,
    poker_stack: int = 0,
    disease: str = "",
) -> str:
    name = mention(user_id, stats.name)
    lines = [
        f"🍆 <b>Профиль</b> · {name}",
        "",
        "<b>Длина</b>",
        f"{stats.current_size} см · #{stats.rank}",
        f"Рост +{stats.total_grown} / потери −{stats.total_lost}",
    ]
    if stats.best_day is not None and stats.worst_day is not None:
        lines.append(f"Бросок: лучший {signed(stats.best_day)} · худший {signed(stats.worst_day)}")
    lines.extend(
        [
            "",
            "<b>Состояние</b>",
            f"{stats.net_worth} см · #{stats.net_rank}",
            "",
            "<b>Игры</b>",
            f"/dick: {stats.plays} · игровых дней {stats.days_played}",
        ]
    )
    if stats.duels_total:
        lines.append(f"Дуэли: {stats.wins}–{stats.losses} · {stats.winrate:.0%} побед")
    if stats.stolen_total or stats.lost_in_duels:
        lines.append(f"В дуэлях: +{stats.stolen_total} / −{stats.lost_in_duels} см")
    if stats.diseases_caught:
        lines.append(f"Заражений: {stats.diseases_caught}")
    if bank_line:
        lines.extend(["", bank_line])
    if poker_stack:
        lines.append(f"🃏 За столом: {poker_stack} см")
    if disease:
        lines.append(f"🦠 Сейчас: {html.escape(disease)}")
    return "\n".join(lines)


def global_profile(stats) -> str:
    lines = [f"🌐 <b>{html.escape(stats.name)}</b>"]
    if stats.is_banned:
        reason = html.escape(stats.ban_reason or "без причины")
        until = (
            f" до {datetime.fromtimestamp(stats.ban_until):%d.%m.%Y %H:%M}"
            if stats.ban_until
            else " навсегда"
        )
        lines.append(f"⛔ Блокировка{until}: {reason}")
    if stats.chats:
        lines.extend(["", "<b>Чаты</b>"])
        for chat in stats.chats:
            lines.append(
                f"• {html.escape(chat.title)}: {chat.size} см (#{chat.rank}) · "
                f"состояние {chat.net_worth} (#{chat.net_rank})"
            )
    lines.extend(
        [
            "",
            "<b>Игры</b>",
            f"/dick: {stats.plays} · +{stats.total_grown} / −{stats.total_lost} см",
        ]
    )
    if stats.best_roll is not None and stats.worst_roll is not None:
        lines.append(
            f"Бросок: лучший {signed(stats.best_roll)} · худший {signed(stats.worst_roll)}"
        )
    if stats.duels_total:
        lines.append(f"Дуэли: {stats.wins}–{stats.losses} · {stats.winrate:.0%} побед")
    if stats.infections:
        lines.append(f"Заражений: {stats.infections}")
    lines.append(f"Рекорд длины: {stats.best_size_ever} см")
    return "\n".join(lines)


def top_caption(data: analytics.Dashboard) -> str:
    leaders = [(label, value) for label, value in data.metrics if label.startswith("#")][:3]
    lines = [f"🏁 <b>Гонка по состоянию · {analytics.PERIODS[data.period]}</b>"]
    for label, value in leaders:
        lines.append(f"{html.escape(label)} {html.escape(value)}")
    movements: list[tuple[float, str]] = []
    for name, values in data.series:
        if len(values) >= 2:
            movements.append((values[-1] - values[0], name))
    if movements:
        delta, name = max(movements, key=lambda item: abs(item[0]))
        lines.extend(["", f"Крупнейший ход: <b>{html.escape(name)} {delta:+g} см</b>"])
    return "\n".join(lines)
