"""Centralized user-facing strings.

All Russian text the bot sends to users lives here so the tone stays consistent
and copy can be edited in one place. Game mechanics (disease modifiers, odds,
etc.) stay in their own modules — only the *display* strings are centralized.

The deliberately crude meme humour is intentional and preserved; only grammar,
length, logic and term consistency were cleaned up.
"""

from __future__ import annotations

import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Primary slang term for the system/service messages. The humour arrays below
# keep their own varied wording on purpose.
DICK = "писюн"

# Max lengths for free-text admin input.
MAX_NAME_LEN = 64
MAX_QUERY_LEN = 64
MAX_BAN_REASON_LEN = 200
MAX_PUBLIC_LABEL_LEN = 80


def _zone(tz: str | None) -> ZoneInfo:
    return ZoneInfo(key=tz or os.environ.get("TZ", "Europe/Moscow"))


def fmt_datetime(ts: int, tz: str | None = None) -> str:
    return datetime.fromtimestamp(ts, _zone(tz)).strftime("%d.%m.%Y %H:%M")


def fmt_date(ts: int, tz: str | None = None) -> str:
    return datetime.fromtimestamp(ts, _zone(tz)).strftime("%d.%m.%Y")


# --------------------------------------------------------------------- /dick ---


def dick_already_today(mention: str, size: int, rank: int, remaining: str) -> str:
    return (
        f"{mention}, твой {DICK} равен {size} см.\n"
        f"Ты занимаешь {rank} место в топе.\n"
        f"Попробуй через {remaining}"
    )


def dick_grew(delta: int) -> str:
    return f"вырос на {delta} см"


def dick_funded_growth(result) -> str:
    if result.clipped:
        return (
            f"мог вырасти на {result.nominal} см, но местная Корпорация уже сосёт "
            f"пустую кассу: выдали {result.credited} (эмиссия {result.emitted}, касса "
            f"{result.corporation_paid}), а {result.clipped} см засунули обратно в генератор"
        )
    return (
        f"вырос на {result.credited} см (эмиссия {result.emitted}, "
        f"Корпорация отстегнула {result.corporation_paid})"
    )


def dick_shrank(delta: int) -> str:
    return f"уменьшился на {abs(delta)} см"


def dick_debt_assessed(amount: int, due_at: int, defaulted: bool) -> str:
    if defaulted:
        return (
            f"снова обмяк на {amount} см, но резать уже нечего — Корпорация немедленно "
            f"накинула их на твою просроченную долговую шею"
        )
    return (
        f"обмяк на {amount} см; отрезать пока не стали, зато записали долг до "
        f"{fmt_datetime(due_at)} — наслаждайся арендованным достоинством"
    )


def dick_pisyago_assessed(loss: int, covered: int, debt: int, due_at: int, defaulted: bool) -> str:
    if debt <= 0:
        return (
            f"обмяк на {loss} см, но ПИСЯГО признало это страховым случаем и "
            f"прикрыло все {covered} см — долг нулевой, позор полноценный"
        )
    if defaulted:
        return (
            f"обмяк на {loss} см; ПИСЯГО прикрыло {covered}, а оставшиеся {debt} см "
            "Корпорация пришила к твоей уже просроченной долговой заднице"
        )
    return (
        f"обмяк на {loss} см; ПИСЯГО прикрыло {covered}, а оставшиеся {debt} см "
        f"ушли в долг до {fmt_datetime(due_at)}"
    )


def dick_pisyago_status(result) -> str:
    if result.remaining <= 0:
        return (
            f"🛡 ПИСЯГО выскоблено досуха; запас вернётся {fmt_datetime(result.reset_at)}. "
            "До тех пор страхуйся молитвой, финансовый огрызок."
        )
    return (
        f"🛡 ПИСЯГО: покрытие {result.coverage_pct}%, в запасе ещё "
        f"{result.remaining} см до {fmt_datetime(result.reset_at)}."
    )


def dick_result(mention: str, change_text: str, size: int, rank: int, remaining: str) -> str:
    return (
        f"{mention}, твой {DICK} {change_text}.\n"
        f"Теперь он равен {size} см.\n"
        f"Ты занимаешь {rank} место в топе.\n"
        f"Следующая попытка завтра, через {remaining}!"
    )


# ---------------------------------------------------------------------- /top ---

TOP_EMPTY = "😥 Пока нет игроков\nПрисоединяйся — напиши /dick"
TOP_HEADER = "🏆 <b>Топ-10 по чистому состоянию:</b>\n"


def top_line(rank: int, name: str, tag: str, size: int) -> str:
    return f"{rank}. <b>{html.escape(name)}{tag}</b> ({size} см)"


# ----------------------------------------------------------------------- /me ---


def profile_not_played(mention: str) -> str:
    return f"{mention} ещё не играл. Измерь {DICK} командой /dick!"


def profile_header(name: str) -> str:
    return f"Профиль {name}"


def profile_size(size: int, rank: int) -> str:
    return f"Длина: {size} см ({rank} место по длине)"


def profile_wealth(value: int, rank: int) -> str:
    return f"Состояние: {value} см ({rank} место по состоянию)"


def profile_plays(plays: int, days: int) -> str:
    return f"Бросков /dick: {plays} за {days} дн."


def profile_growth(grown: int, lost: int) -> str:
    return f"Всего вырос: +{grown} см / потерял: -{lost} см"


def profile_best_worst(best: int, worst: int) -> str:
    return f"Лучший бросок: {best:+d} см | худший: {worst:+d} см"


def profile_duels(total: int, wins: int, losses: int, winrate: float) -> str:
    return f"Дуэли: {total} (W{wins}/L{losses}, винрейт {winrate:.0%})"


def profile_stolen(stolen: int, lost: int) -> str:
    return f"Отжато: +{stolen} см | проиграно: -{lost} см"


def profile_infections(n: int) -> str:
    return f"Заражений: {n}"


def profile_poker_stack(stack: int) -> str:
    return f"За покерным столом: {stack} см — временно не на руках, а под чужими жадными глазами"


def profile_current_disease(name: str) -> str:
    return f"Сейчас болеет: {name}"


def profile_size_timeline(spark: str) -> str:
    return f"Размер во времени: {spark}"


def profile_deltas(n: int, spark: str) -> str:
    return f"Изменения размера ({n} дн.): {spark}"


# ----------------------------------------------------- /me global profile ---

GLOBAL_BUTTON = "🌐 Глобальный профиль"
GLOBAL_EMPTY = f"Ты ещё нигде не играл. Измерь {DICK} командой /dick в любом чате!"


def global_header(name: str) -> str:
    return f"🌐 Глобальный профиль {name}"


GLOBAL_CHATS_HEADER = "Чаты:"
GLOBAL_NO_CHATS = "Пока ни в одном чате нет накоплений."


def global_chat_line(title: str, size: int, rank: int, net_worth: int, net_rank: int) -> str:
    return f"• {title}: длина {size} см (#{rank}), состояние {net_worth} см (#{net_rank})"


def global_plays(plays: int, grown: int, lost: int) -> str:
    return f"Бросков /dick: {plays} (вырос +{grown} / потерял -{lost} см)"


def global_best_worst(best: int, worst: int) -> str:
    return f"Лучший бросок: {best:+d} см | худший: {worst:+d} см"


def global_duels(total: int, wins: int, losses: int, winrate: float) -> str:
    return f"Дуэли: {total} (W{wins}/L{losses}, винрейт {winrate:.0%})"


def global_infections(n: int) -> str:
    return f"Заражений всего: {n}"


def global_record(best_size: int) -> str:
    return f"Рекорд размера: {best_size} см"


def global_tenure(date_str: str) -> str:
    return f"В игре с {date_str}"


def global_ban_until(date_str: str, reason: str) -> str:
    return f"⛔ Заблокирован до {date_str}: {reason}"


def global_ban_forever(reason: str) -> str:
    return f"⛔ Заблокирован навсегда: {reason}"


BAN_NO_REASON = "без причины"


# -------------------------------------------------------------- /start /help ---

START = (
    "👋 Привет! Это бот-игра.\n\n"
    "Бот может писать тебе в личку — например, предупредить о блокировке "
    "рассылки или ответить по обращению в поддержку.\n\n"
    "Добавь меня в групповой чат и отправь /help, чтобы увидеть список команд."
)

HELP = (
    "Команды:\n"
    "/help — вывести этот текст\n"
    "/dick — испытать удачу\n"
    "/casino [ставка] — крутить слот или сохранить ставку\n"
    "/duel [ставка] — вызвать на дуэль (ответом на сообщение)\n"
    "/poker — ПИСЮН-HOLDEM на сантиметры\n"
    "/me — твой профиль и статистика\n"
    "/stats — подробная статистика, графики и CSV в личке\n"
    "/top — топ-10 по чистому состоянию и недельная гонка\n"
    "/season — предварительные итоги и архив недельных сезонов\n"
    "/bank — вклады, кредиты и текущий баланс\n"
    "/corp — состояние Корпорации\n"
    "/ping — ping-pong\n"
    "/setbcast — (админам, внутри темы) выбрать тему для рассылок\n"
    "/unsetbcast — (админам) сбросить тему рассылок\n"
    "/settings — (админам) интерактивные настройки чата\n"
    "/gameconfig — (админам) текстовый alias настроек\n"
    "/localban, /localunban — (админам) блокировка игрока в этом чате\n"
    "/resetleaderboard — (админам) обнулить таблицу чата"
)

HELP_ADMIN = "\n\n/admin — панель глобального администратора (в личке с ботом)"


# ------------------------------------------------------ /setbcast /unsetbcast ---

BCAST_NOT_ADMIN = "Менять тему рассылки может только администратор чата."
BCAST_NEED_TOPIC = (
    "Выполни эту команду внутри нужной темы форума — именно туда будут приходить рассылки."
)
BCAST_SET = "✅ Эта тема выбрана для рассылок."
BCAST_CLEARED = "Тема рассылки сброшена. Теперь будет использоваться самая активная тема."
BCAST_NOT_SET = "Тема рассылки и так не была задана."


# ------------------------------------------------------------------ registry ---

DM_GATE_BUTTON = "✍️ Написать боту"
DM_GATE = "👋 Чтобы пользоваться ботом, сначала напиши ему в личку (кнопка ниже, затем /start)."


# ------------------------------------------------------- local moderation -------

# Shown to a player blocked locally (per-chat) by a chat admin.
LOCAL_BANNED = "🚫 Админ чата заблокировал тебя в этой игре."

# Transient "you're on cooldown" reply — shown at most once per cooldown window
# and self-deleted after a few seconds so it doesn't clutter the chat.
COOLDOWN_NOTICE = "⏳ Не так быстро — команда на кулдауне. Подожди немного."
CALLBACK_INVALID = "Эта кнопка устарела или повреждена. Открой панель заново."

MOD_NEED_TARGET = (
    "Ответь командой на сообщение игрока или укажи его числовой id: "
    "<code>/localban 123456789</code>."
)
MOD_TARGET_NOT_FOUND = "Игрок не найден в этом чате."


def mod_localban_done(name: str) -> str:
    return f"🚫 <b>{html.escape(name)}</b> заблокирован в игре этого чата."


def mod_localban_already(name: str) -> str:
    return f"<b>{html.escape(name)}</b> уже заблокирован."


def mod_localunban_done(name: str) -> str:
    return f"✅ <b>{html.escape(name)}</b> снова может играть."


def mod_localunban_already(name: str) -> str:
    return f"<b>{html.escape(name)}</b> и так не заблокирован."


def mod_reset_done(count: int) -> str:
    return f"♻️ Таблица обнулена: сброшено размеров у {count} игроков."


# ------------------------------------------------------------- /gameconfig ------

GAMECONFIG_USAGE = (
    "Настройки чата. Использование:\n"
    "<code>/gameconfig</code> — показать текущие\n"
    "<code>/gameconfig &lt;ключ&gt; &lt;значение&gt;</code> — изменить\n\n"
    "Ключи:\n"
    "• <code>tz</code> — часовой пояс (например <code>Europe/Moscow</code>)\n"
    "• <code>diseases</code> — болезни <code>on</code>/<code>off</code>\n"
    "• <code>poker</code> — покер <code>on</code>/<code>off</code>\n"
    "• <code>casino</code> — казино <code>on</code>/<code>off</code>\n"
    "• <code>duel_stake</code> — ставка дуэли по умолчанию (1–1000)\n"
    "• <code>duel_timeout</code> — таймаут дуэли в секундах (10–600)"
)


def gameconfig_current(
    tz: str,
    diseases: bool,
    stake: int,
    timeout: int,
    poker: bool = True,
    casino: bool = True,
) -> str:
    on_off = "on" if diseases else "off"
    poker_on_off = "on" if poker else "off"
    casino_on_off = "on" if casino else "off"
    return (
        "⚙️ Текущие настройки чата:\n"
        f"• tz: <code>{html.escape(tz)}</code>\n"
        f"• diseases: <code>{on_off}</code>\n"
        f"• poker: <code>{poker_on_off}</code>\n"
        f"• casino: <code>{casino_on_off}</code>\n"
        f"• duel_stake: <code>{stake}</code>\n"
        f"• duel_timeout: <code>{timeout}</code>"
    )


def gameconfig_set_ok(key: str, value: str) -> str:
    return f"✅ <code>{html.escape(key)}</code> = <code>{html.escape(value)}</code>"


GAMECONFIG_ERRORS = {
    "unknown_key": "Неизвестный ключ. Доступны: tz, diseases, poker, casino, duel_stake, duel_timeout.",
    "bad_tz": "Неизвестный часовой пояс. Пример: Europe/Moscow.",
    "bad_bool": "Ожидается on или off.",
    "bad_int": "Ожидается целое число.",
    "out_of_range": "Значение вне допустимого диапазона.",
}


# ------------------------------------------------------------------- /casino ---

CASINO_GROUP_ONLY = "🎰 Казино работает только в группах — в одиночку кассу не раскачаешь."
CASINO_BAD_STAKE = "🎰 Ставка должна быть целым числом от 1 до 50 см."
CASINO_DISABLED = "🎰 Казино отключено администраторами этого чата."
CASINO_NO_PLAYER = "🎰 Сначала сыграй в /dick — без размера к автомату не подпускают."
CASINO_NO_SIZE = "🎰 На руках пусто. Сначала отрасти хоть что-нибудь через /dick."
CASINO_INSUFFICIENT = "🎰 На руках меньше выбранной ставки. Уменьши её через /casino 5."
CASINO_RECOVERY = "🎰 Корпорация восстанавливает кассу после распила. Крутки пока закрыты."
CASINO_SEND_FAILED = "🎰 Telegram не запустил слот. Деньги не списаны."
CASINO_SPIN_CANCELED = "⚠️ Крутка отменена: результат не рассчитан, деньги не изменились."
CASINO_BUTTON_INVALID = "Эта кнопка казино устарела или повреждена. Вызови /casino заново."
CASINO_PAYOUT_RULES = (
    "🎰 <b>Как считаются выигрыши</b>\n"
    "• 7️⃣7️⃣7️⃣ — выплата ×18\n"
    "• ровно две 7️⃣ — выплата ×3\n"
    "• три одинаковых, кроме 7️⃣, — выплата ×5\n"
    "• остальные комбинации — ×0\n\n"
    "Множитель применяется к ставке; выплата уже включает её. "
    "Чистый итог = выплата − ставка."
)


def casino_stake_saved(stake: int) -> str:
    return (
        f"✅ Ставка казино сохранена: <b>{stake} см</b>.\n"
        "Теперь <code>/casino</code> крутит слот с этой ставкой.\n"
        "Выплаты: 7️⃣7️⃣7️⃣ ×18 · две 7️⃣ ×3 · три одинаковых ×5."
    )


def _casino_rules_suffix(rules_url: str | None) -> str:
    if rules_url:
        safe_url = html.escape(rules_url, quote=True)
        return f' · <a href="{safe_url}">Как считаются выигрыши</a>'
    return " · 777 ×18 · две 7 ×3 · три одинаковых ×5"


def casino_loss(stake: int, rules_url: str | None = None) -> str:
    return f"💸 Сняли <b>{stake} см</b>{_casino_rules_suffix(rules_url)}"


def casino_cooldown(retry_after: int) -> str:
    return f"⏳ Автомат ещё крутится. Подожди {max(1, retry_after)} сек."


def casino_result(result, rules_url: str | None = None) -> str:
    net = f"+{result.net}" if result.net > 0 else str(result.net).replace("-", "−")
    return (
        f"🎉 Чистыми <b>{net} см</b> · выплата <b>{result.gross_payout} см</b>"
        f"{_casino_rules_suffix(rules_url)}"
    )


def casino_bail_in_notice(name: str, payout: int, status: str) -> str:
    status_label = {
        "healthy": "нормальный",
        "recovery": "восстановление",
    }.get(status, "неизвестный")
    return (
        f"🚨 Выигрыш <b>{html.escape(name)}</b> на <b>{payout} см</b> вызвал распил "
        f"Корпорации. Статус кассы: <b>{status_label}</b>.\n"
        "Персональные остатки вкладчиков не публикуются."
    )


# ---------------------------------------------------------------------- /duel ---

VICTORY_LINES = [
    "ОПА {winner} ПОБЕЖДАЕТ {loser} в жестокой схватке на письках!",
    "БАХ! {winner} УНИЧТОЖАЕТ {loser} в пиписечной дуэли!",
    "ВНЕЗАПНО {winner} РАЗНОСИТ {loser} в пух и прах!",
    "ТРАХ-БАБАХ! {winner} НЕ ОСТАВЛЯЕТ ШАНСОВ {loser}!",
    "ФАТАЛИТИ! {winner} ДОБИВАЕТ {loser} в неравном бою!",
    "ХЛОБЫСЬ! {winner} РАСКАТЫВАЕТ {loser} как блинчик!",
    "ШМЯК! {winner} ВТАПТЫВАЕТ {loser} в грязь лицом!",
    "ХРЯСЬ! {winner} ВЫНОСИТ {loser} одной левой!",
    "ПШШШ... {winner} МЕТОДИЧНО УНИЧТОЖАЕТ {loser}!",
    "БДЫЩЬ! {winner} НАНОСИТ СОКРУШИТЕЛЬНЫЙ УРОН {loser}!",
    "ПИУ-ПИУ! {winner} РАССТРЕЛИВАЕТ {loser} в упор!",
    "ХАДУКЕН! {winner} ПРОБИВАЕТ ЗАЩИТУ {loser}!",
    "КРИТ! {winner} НАНОСИТ КРИТИЧЕСКИЙ УДАР {loser}!",
    "RAMPAGE! {winner} НЕ ОСТАНОВИТЬ... {loser} ПОВЕРЖЕН!",
    "GODLIKE! {winner} ВОЗНОСИТСЯ НАД {loser}!",
]

TECHNIQUE_LINES = [
    "Применил СКОРОСТРЕЛ — удар был слишком быстр для {loser_name}.",
    "Использовал ТЯЖЕЛУЮ АРТИЛЛЕРИЮ — {loser_name} не выдержал напора.",
    "Сработала тактика ВНЕЗАПНОГО ПРОНИКНОВЕНИЯ — {loser_name} не успел сгруппироваться.",
    "Провёл КОНТР-АРГУМЕНТ — {loser_name} опешил от такого поворота.",
    "Применил ПРИЁМ ТРЁХСОТ СПАРТАНЦЕВ — {loser_name} отброшен назад.",
    "Включил режим БЕРСЕРКА — {loser_name} в нокауте.",
    "Исполнил КОМБО x3 — {loser_name} разорван в клочья.",
    "Сделал ОБХОДНОЙ МАНЁВР — {loser_name} атакован с фланга.",
    "Нажал КНОПКУ УЛЬТЫ — {loser_name} аннигилирован.",
    "Применил ДИПЛОМАТИЮ — не помогла, пришлось бить. {loser_name} проиграл.",
    "Устроил АРТ-ОБСТРЕЛ — накрыло {loser_name} по полной.",
    "Поймал ВТОРОЕ ДЫХАНИЕ — {loser_name} такого не ожидал.",
    "Активировал ЧИТ-КОДЫ — {loser_name} уже пишет жалобу администрации.",
    "Ушёл в СТЕЛС — {loser_name} даже не понял, откуда прилетело.",
    "Врубил ТУРБО-РЕЖИМ — {loser_name} снесён ударной волной.",
]

STEAL_LINES = [
    "ОТОБРАЛ {stolen} см у {loser}",
    "ОТЖАЛ {stolen} см у {loser}",
    "ЭКСПРОПРИИРОВАЛ {stolen} см у {loser}",
    "КОНФИСКОВАЛ {stolen} см у {loser}",
    "СПИЗДИЛ {stolen} см у {loser}",
    "ОТЖАРИЛ {stolen} см у {loser} без права на возврат",
    "ВЫРВАЛ {stolen} см у {loser} с мясом",
    "СКРУТИЛ {stolen} см у {loser} в баранку",
    "ОТКУСИЛ {stolen} см у {loser} как сникерс",
]

CORP_LINES = [
    "Корпорация Ненавязчиво Забирает Свои {tax} см (это бизнес, ничего личного).",
    "Ну и мы, как честная корпорация, скромно взяли комиссию: {tax} см.",
    "Агенты корпорации уже списали {tax} см комиссии. Спасибо за сотрудничество.",
    "Комиссия корпорации: {tax} см. Без обид, это просто бизнес.",
    "Налог на воздух, НДС на письку, пенсионный сбор... короче {tax} см наших.",
    "Отдел комплаенс списал {tax} см. Таковы правила корпоративной этики.",
    "Юридический отдел требует {tax} см за оформление протокола дуэли.",
    "Бухгалтерия уже перевела {tax} см на офшорный счет. Все чисто.",
]

# (lo, hi, reaction_mod, comment)
REACTION_TIERS = [
    (0, 3, -0.15, "МГНОВЕННАЯ реакция! {loser} почти увернулся... но не совсем."),
    (3, 8, -0.07, "Быстрая реакция, {loser} пытался уклониться."),
    (8, 20, 0.00, "Обычная реакция — ни туда ни сюда."),
    (20, 40, 0.05, "Слегка замешкался... {loser} явно отвлекся на котиков."),
    (40, 999, 0.12, "ОЧЕНЬ долго думал... {loser} залип в телефоне и поплатился."),
]

DUEL_DEFAULT_REACTION = "Обычная реакция. Ничего особенного."
DUEL_EXPIRED_NOBODY = "Вызов истёк. Никто так и не откликнулся."
DUEL_INVALID = "Вызов уже недействителен."
DUEL_NOT_YOURS = "Этот вызов не тебе!"
DUEL_OWN = "Нельзя принять собственный вызов."
DUEL_TIMED_OUT = "Вызов просрочен. Дуэль отменена."
DUEL_SELF = "Нельзя вызвать на дуэль самого себя. Это было бы странно."
DUEL_ACCEPT_BUTTON = "-- ПРИНЯТЬ ВЫЗОВ --"
DUEL_TOO_MANY = "У тебя слишком много активных вызовов. Дождись их завершения."
DUEL_BAD_STAKE = "Ставка должна быть целым числом больше нуля. Например: /duel 10"


def duel_measure_first(mention: str) -> str:
    return f"Сначала измерь {DICK} командой /dick, {mention}!"


def duel_zero_size(mention: str) -> str:
    return f"Твой {DICK} размером 0 см. Дуэль невозможна. Попробуй /dick."


def duel_target_measure_first(mention: str) -> str:
    return f"Сначала измерь {DICK} командой /dick, {mention}!"


def duel_target_zero(mention: str) -> str:
    return f"У {mention} {DICK} размером 0 см. Дуэль невозможна."


DUEL_ACCEPT_MEASURE_FIRST = "Сначала измерь писюн командой /dick!"
DUEL_ACCEPT_ZERO = "Твой писюн размером 0 см. Дуэль невозможна."


def duel_open_challenge(mention: str, stake: int, attacker_size: int, timeout: int) -> str:
    return (
        f"{mention} бросает ОТКРЫТЫЙ ВЫЗОВ!\n"
        f"Первый смельчак, нажавший кнопку — тот и соперник.\n\n"
        f"Ставка: {stake} см с каждого.\n"
        f"{attacker_size} см vs ??? см (шансы: ? / ?)\n\n"
        f"На раздумья {timeout} секунд."
    )


def duel_directed_challenge(
    attacker: str,
    defender: str,
    defender_name: str,
    stake: int,
    attacker_size: int,
    defender_size: int,
    base_chance: float,
    timeout: int,
) -> str:
    return (
        f"{attacker} вызывает {defender} на дуэль!\n\n"
        f"Ставка: {stake} см с каждого.\n"
        f"{attacker_size} см vs {defender_size} см "
        f"(шанс: {base_chance:.0%} / {1 - base_chance:.0%})\n\n"
        f"У {defender_name} есть {timeout} секунд чтобы принять вызов.\n"
        f"Реакция повлияет на исход!"
    )


def duel_disease_note(name: str, duel_mod: float, who: str) -> str:
    return f"{who}: {name} даёт {duel_mod:+.0%} к шансу атакующего"


def duel_result(
    victory_line: str,
    technique_line: str,
    steal_line: str,
    winner_profit: int,
    corp_tax: int,
    attacker_name: str,
    attacker_was: int,
    attacker_now: int,
    attacker_tag: str,
    defender_name: str,
    defender_was: int,
    defender_now: int,
    defender_tag: str,
    base_chance: float,
    final_chance: float,
) -> str:
    return (
        f"{victory_line}\n"
        f"{technique_line}\n\n"
        f"{steal_line}\n\n"
        f"Из них:\n"
        f"-- победитель получил +{winner_profit} см (x1.5 от ставки)\n"
        f"-- корпорация забрала {corp_tax} см\n\n"
        f"Итог:\n"
        f"{attacker_name} было {attacker_was} см, теперь {attacker_now} см{attacker_tag}\n"
        f"{defender_name} было {defender_was} см, теперь {defender_now} см{defender_tag}\n\n"
        f"Базовый шанс атакующего: {base_chance:.0%} | Итоговый: {final_chance:.0%}"
    )


# ------------------------------------------------------------------- болезни ---

# id -> (display name, catch message). Mechanics live in models/disease.py.
DISEASE_TEXT: dict[str, tuple[str, str]] = {
    "syphilis": (
        "СИФИЛИС",
        "ТЫ ПОДХВАТИЛ СИФИЛИС ХАХАХА! Писька гниёт, рост замедлен, в дуэлях штраф. АРГХ!",
    ),
    "fracture": (
        "ПЕРЕЛОМ ПИСЬКИ",
        "ТЫ СЛОМАЛ ПИСЬКУ! Гипс на 2 дня. Рост заблокирован, шансы в дуэлях ниже.",
    ),
    "fungus": (
        "ГРИБОК",
        "У ТЕБЯ ГРИБОК НА ПИСЬКЕ! Чешется и воняет. Заразно! Но расти не мешает.",
    ),
    "valgus": (
        "ВАЛЬГУСНАЯ ДЕФОРМАЦИЯ",
        "ПИСЬКА ИСКРИВИЛАСЬ! Как турецкий ятаган. Теперь она под углом 45 градусов.",
    ),
    "piercing": (
        "ПИРСИНГ",
        "ТЕБЕ СДЕЛАЛИ ИНТИМНЫЙ ПИРСИНГ! +10% к росту и +5% к шансам в дуэлях. Стильно!",
    ),
    "gonorrhea": (
        "ГОНОРЕЯ",
        "У ТЕБЯ ГОНОРЕЯ! Жжение при использовании /dick. Заразная штука.",
    ),
}


def disease_tag_text(name: str, days_left: int) -> str:
    return f" [{name} ещё {days_left} дн]"


# ------------------------------------------------------------------- /admin ---

ADMIN_TITLE = "🛠 Админ-панель"

BTN_CHATS = "💬 Чаты"
BTN_FIND = "🔎 Поиск игрока"
BTN_STATS = "📊 Статистика"
BTN_ECONOMY = "📈 Экономика"
BTN_BCAST = "📢 Рассылка"
BTN_HOME = "🏠 Меню"
BTN_PREV = "« Назад"
BTN_NEXT = "Вперёд »"
BTN_FIRST = "⏮"
BTN_LAST = "⏭"


def pager_indicator(page: int, pages: int, total: int) -> str:
    return f"{page + 1}/{pages} ({total})"


BTN_BACK_LIST = "« К списку"
BTN_BACK_CHAT = "« К чату"
BTN_BACK_FIND = "« К результатам поиска"
BTN_BACK = "« Назад"
BTN_YES = "✅ Да"
BTN_CANCEL = "❌ Отмена"
BTN_OWN_REASON = "✏️ Своя причина"

BTN_BCAST_HISTORY = "🗂 История рассылок"
BTN_GLOBAL_SETTINGS = "⚙️ Глобальные настройки"

ADMIN_GSET_TITLE = "⚙️ <b>Глобальные настройки</b>\nИзменения применяются сразу для всех чатов."


def gset_field_label(label: str, value: int) -> str:
    return f"{label}: {value}"


# ---- per-chat local bans (global panel) ----

BTN_LOCAL_BANS = "🚫 Локальные баны"
BTN_HEALTH_REFORM = "🥦 ЗОЖ-реформа"
ADMIN_NO_LOCAL_BANS = "В этом чате нет локально забаненных игроков."


def admin_local_bans_page(total: int, page: int) -> str:
    return f"🚫 <b>Локальные баны</b> · всего: {total} · стр. {page + 1}"


def admin_local_unban_btn(name: str) -> str:
    return f"✅ Разбанить {name}"


# ---- sort / filter / breadcrumbs (admin lists) ----

BTN_FILTER = "🔎 Фильтр"
BTN_FILTER_CLEAR = "✖️ Сбросить фильтр"
ADMIN_ENTER_FILTER_CHATS = "Введи часть названия чата для фильтра:"
ADMIN_ENTER_FILTER_PLAYERS = "Введи часть имени игрока для фильтра:"

CHAT_SORTS: list[tuple[str, str]] = [
    ("n", "Имя"),
    ("a", "Актив."),
    ("s", "Игроки"),
    ("c", "Новые"),
]
PLAYER_SORTS: list[tuple[str, str]] = [
    ("s", "Размер"),
    ("n", "Имя"),
    ("a", "Актив."),
]


def crumb(*parts: str) -> str:
    return "🏠 › " + " › ".join(parts)


def sort_btn(label: str, active: bool) -> str:
    return f"• {label}" if active else label


def admin_chats_overview(total: int, active: int, page: int, filt: str | None) -> str:
    head = crumb("Чаты")
    stats = f"Всего: {total} · активных: {active}"
    if filt:
        stats += f" · фильтр: «{html.escape(filt)}»"
    return f"{head}\n{stats} · стр. {page + 1}"


def admin_chat_banned_count(n: int) -> str:
    return f"Локальных банов: {n}"


def admin_filter_note(filt: str, matches: int) -> str:
    return f"🔎 Фильтр: «{html.escape(filt)}» · совпадений: {matches}"


# ---- per-chat settings panel (global panel + local /settings) ----

BTN_CHAT_SETTINGS = "⚙️ Настройки"
BTN_CLOSE = "✖️ Закрыть"
SETTINGS_TITLE = "⚙️ <b>Настройки чата</b>"
SETTINGS_ENTER_TZ = "Введи часовой пояс (например <code>Europe/Moscow</code>):"
SETTINGS_BAD_TZ = "Неизвестный часовой пояс. Пример: Europe/Moscow."
SETTINGS_NOT_ALLOWED = "Только администраторы чата могут менять настройки."


def settings_screen(
    tz: str,
    diseases: bool,
    stake: int,
    timeout: int,
    banking: bool = True,
    poker: bool = True,
    casino: bool = True,
    digest: bool = False,
    digest_weekday: int = 0,
    digest_hour: int = 10,
) -> str:
    on_off = "вкл" if diseases else "выкл"
    bank_off = "вкл" if banking else "выкл"
    poker_off = "вкл" if poker else "выкл"
    casino_off = "вкл" if casino else "выкл"
    digest_off = "вкл" if digest else "выкл"
    weekdays = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    return (
        f"{SETTINGS_TITLE}\n\n"
        f"• Часовой пояс: <code>{html.escape(tz)}</code>\n"
        f"• Болезни: {on_off}\n"
        f"• Банк: {bank_off}\n"
        f"• Покер: {poker_off}\n"
        f"• Казино: {casino_off}\n"
        f"• Ставка дуэли: {stake}\n"
        f"• Таймаут дуэли: {timeout} сек\n"
        f"• Недельные сезоны: {digest_off}, {weekdays[digest_weekday]} {digest_hour:02d}:00"
    )


def settings_btn_diseases(enabled: bool) -> str:
    return f"🦠 Болезни: {'✅' if enabled else '❌'}"


def settings_btn_banking(enabled: bool) -> str:
    return f"🏦 Банк: {'✅' if enabled else '❌'}"


def settings_btn_poker(enabled: bool) -> str:
    return f"🃏 Покер: {'✅' if enabled else '❌'}"


def settings_btn_casino(enabled: bool) -> str:
    return f"🎰 Казино: {'✅' if enabled else '❌'}"


def settings_label_stake(value: int) -> str:
    return f"Ставка дуэли: {value}"


def settings_label_timeout(value: int) -> str:
    return f"Таймаут дуэли: {value} сек"


def settings_btn_tz(tz: str) -> str:
    return f"🕒 Часовой пояс: {tz}"


def settings_btn_digest(enabled: bool) -> str:
    return f"🍆 Недельные сезоны: {'✅' if enabled else '❌'}"


# --------------------------------------------------------------------- poker ---

POKER_DISABLED = "🃏 Покер отключён администраторами этого чата."
POKER_DM_REQUIRED = "Открой личку с ботом — карты в общий чат я тебе не вывалю."
POKER_TURN_NOTICE = (
    "🍆 Твой ход. Шевели единственной рабочей извилиной: таймер не обязан ждать, "
    "пока из жопы родится стратегия."
)
POKER_REQUEST_SENT = (
    "Заявка отправлена. Хозяин изучает твою финансовую и моральную несостоятельность."
)
POKER_REQUEST_APPROVED = "✅ Тебя пустили за стол. Постарайся не расплескать стек."
POKER_REQUEST_DENIED = "🚫 Хозяин решил, что за этим столом органов и так достаточно."
POKER_CREATED = "✅ Стол создан. Пульт и карты будут жить в этом чате."
POKER_CUSTOM_CONFIG = (
    "Пришли пять целых чисел через пробел:\n"
    "<code>бай-ин малый_блайнд большой_блайнд места таймер</code>\n"
    "Например: <code>60 1 2 5 60</code>.\n\n"
    "Ограничения: бай-ин от 10 больших блайндов; блайнды от 1; "
    "мест 2–6; таймер 30–180 секунд."
)
POKER_CUSTOM_RAISE = "Введи итоговую ставку целым числом в указанном диапазоне."
POKER_TOPUP_PROMPT = "Сколько сантиметров добавить в стек? Пришли целое число."
POKER_CLOSED = "🏁 Стол закрыт. Остатки возвращены владельцам."

POKER_ERRORS = {
    "already_seated": "Твоя жопа уже заняла стул за другим столом. Размножаться запретили — позорься последовательно.",
    "not_a_player": "Сначала сыграй /dick в исходном чате. Без замера ты здесь не игрок, а шум из коридора.",
    "locally_banned": LOCAL_BANNED,
    "not_enough": "Не хватает свободных сантиметров. Карманы пустые, понты полные — классический клиент Корпорации.",
    "table_closed": "Этот стол уже закрыт.",
    "table_missing": "Стол не найден или уже растворился в истории.",
    "table_full": "Все стулья заняты. Стоя унижаться правила пока не разрешают.",
    "table_banned": "Хозяин этого стола внёс тебя в маленькую чёрную книжечку.",
    "invite_only": "Сюда входят только по персональному или одноразовому приглашению.",
    "invite_invalid": "Приглашение истекло, уже использовано или предназначалось не тебе.",
    "host_only": "Убери липкие пальцы: эту кнопку трогает хозяин, а не случайный хуй с прохода.",
    "request_expired": "Заявка уже истекла. Пусть игрок попросится снова.",
    "request_missing": "Заявка больше не существует.",
    "not_seated": "Ты не сидишь за этим столом. Отойди от кнопок, цифровой бомж.",
    "hand_active": "Дождись конца текущей раздачи.",
    "empty_stack": "Сначала докупи стек — с пустыми руками тут только шутят.",
    "not_everyone_ready": "Нужно хотя бы двое готовых. Пока здесь один азартный орган и хор трусливых мошонок.",
    "hand_missing": "Активная раздача уже закончилась.",
    "stale_action": "Ты нажал протухшую кнопку. Стол уже уехал вперёд, а ты снова догоняешь собственную мысль.",
    "bad_amount": "Напиши положительное целое число. Даже твой калькулятор сейчас смотрит на тебя с презрением.",
    "bad_seats": "За столом может быть от 2 до 6 мест.",
    "bad_blinds": "Блайнды должны быть положительными, большой не меньше малого.",
    "buyin_too_small": "Бай-ин должен быть не меньше десяти больших блайндов.",
    "amount_too_large": "Число настолько раздуто, что даже база данных не поверила твоему комплексу величия.",
    "bad_timeout": "Таймер хода может быть от 30 до 180 секунд.",
    "bad_config": "Настройки стола не прошли проверку.",
    "cannot_kick_host": "Чтобы выгнать хозяина, хозяину придётся закрыть весь стол.",
    "not_your_turn": "Не твой ход. Сядь на руки, раз они бегут быстрее головы.",
    "cannot_check": "Нахаляву отсидеться не выйдет: плати колл или отползай в пас, финансовый слизень.",
    "nothing_to_call": "Коллировать нечего.",
    "cannot_raise": "Сейчас рейз недоступен.",
    "not_enough_stack": "В стеке нет столько сантиметров.",
    "raise_too_small": "Это не рейз, а жалкое подёргивание. Поднимай нормально или вытряхивай весь стек.",
    "amount_required": "Укажи сумму рейза.",
    "unknown_action": "Неизвестное действие.",
}


def poker_error(code: str) -> str:
    return POKER_ERRORS.get(code, "Покерный механизм недовольно хрустнул. Попробуй ещё раз.")


BTN_UNBAN_USER = "✅ Разбан юзера"
BTN_MODE_ALL = "🌐 Все чаты"
BTN_MODE_GROUPS = "👥 Только группы"
BTN_MODE_DM = "✉️ Только личка"
BTN_MODE_ACTIVE = "🔥 Только активные"

BTN_RESET_CHAT = "🧨 Сброс чата"
BTN_UNBAN = "✅ Разбан"
BTN_BAN_CHAT = "🚫 Бан чата"
BTN_SET_SIZE = "🔢 Задать размер"
BTN_SET_NAME = "✏️ Имя"
BTN_SET_PUBLIC_LABEL = "🏷 Публичный лейбл"
BTN_GIVE_DISEASE = "🦠 Выдать болезнь"
BTN_CURE = "💊 Вылечить"
BTN_RESET_PLAYER = "♻️ Сброс игрока"
BTN_DELETE_PLAYER = "🗑 Удалить игрока"
BTN_BAN_USER = "🚫 Бан юзера"

# Ban duration picker.
BAN_DURATIONS: list[tuple[str, str, int | None]] = [
    ("1h", "1 час", 3600),
    ("1d", "1 день", 86400),
    ("7d", "7 дней", 604800),
    ("30d", "30 дней", 2592000),
    ("forever", "Навсегда", None),
]
BAN_DURATION_SECS: dict[str, int | None] = {d_id: secs for d_id, _, secs in BAN_DURATIONS}

# Preset ban reasons (id -> human text). "Своя причина" is handled via FSM.
BAN_REASONS: list[tuple[str, str]] = [
    ("spam", "Спам"),
    ("flood", "Флуд"),
    ("ads", "Реклама"),
    ("abuse", "Оскорбления/токсичность"),
    ("nsfw", "NSFW/непотребство"),
    ("other", "Другое"),
]
BAN_REASON_TEXT: dict[str, str] = {rid: txt for rid, txt in BAN_REASONS}

ADMIN_PLAYER_NOT_FOUND = "Игрок не найден."
ADMIN_NO_PLAYERS = "Нет игроков."
ADMIN_PICK_DISEASE = "Выбери болезнь:"
ADMIN_PICK_BAN_DURATION = "На какой срок забанить? Выбери длительность:"
ADMIN_ENTER_SIZE = "Введи новый размер (целое число):"
ADMIN_SIZE_NOT_INT = "Нужно целое число. Отменено."
ADMIN_ENTER_NAME = "Введи новое имя:"
ADMIN_NAME_EMPTY = "Пустое имя. Отменено."
ADMIN_ENTER_PUBLIC_LABEL = (
    "Введи публичный лейбл одной строкой (до 80 символов).\n"
    "Отправь <code>-</code>, чтобы убрать его."
)
ADMIN_PUBLIC_LABEL_INVALID = "Нужна непустая строка без переносов, не длиннее 80 символов."
ADMIN_ENTER_BAN_REASON = "Введи причину бана:"
ADMIN_ENTER_BAN_CHAT_REASON = "Введи причину бана чата:"
ADMIN_REASON_EMPTY = "Пустая причина. Отменено."
ADMIN_ENTER_FIND = "Введи ID игрока или часть имени:"
ADMIN_FIND_EMPTY = "Пустой запрос. Отменено."
ADMIN_FIND_NONE = "Ничего не найдено."
ADMIN_FIND_LOST = "Запрос поиска потерян. Повтори поиск через меню."
ADMIN_ENTER_BCAST = "Введи текст рассылки (HTML). Дальше выберешь, кому отправить:"
ADMIN_BCAST_EMPTY = "Пустой текст. Отменено."
ADMIN_BCAST_LOST = "Текст рассылки потерян. Отменено."
ADMIN_BCAST_STARTED = "📢 Рассылка началась…"
ADMIN_PICK_BCAST_MODE = "📢 Кому отправить рассылку?"
ADMIN_BCAST_NO_HISTORY = "Рассылок ещё не было."

# Broadcast target mode -> human label.
BCAST_MODE_LABELS: dict[str, str] = {
    "all": "все чаты",
    "groups": "только группы",
    "dm": "только личка",
    "active": "только активные",
}

# Broadcast knobs (centralized so handlers/repos share one source of truth).
MAX_BCAST_PREVIEW_LEN = 200
ADMIN_BCAST_AUTO_TOPIC = (
    "ℹ️ Тема для рассылок не задана — сообщения идут в самую активную тему. "
    "Чтобы выбрать тему, отправьте в ней /setbcast."
)


def admin_chats_page(total: int, page: int) -> str:
    return f"Всего чатов: {total}. Страница {page + 1}."


def admin_chat_header(title: str, chat_id: int) -> str:
    return f"💬 <b>{html.escape(title)}</b> (id {chat_id})"


def admin_chat_stats(players: int, total_size: int, biggest: int) -> str:
    return f"Игроков: {players} | сумма: {total_size} | максимум: {biggest}"


def admin_player_line(name: str, tag: str, size: int, user_id: int) -> str:
    return f"{html.escape(name)}{tag} — {size} см (id {user_id})"


def admin_player_header(
    name: str,
    tag: str,
    user_id: int,
    username: str,
    size: int,
    chat_id: int,
    public_label: str | None = None,
) -> str:
    header = (
        f"👤 <b>{html.escape(name)}</b>{tag}\n"
        f"id: {user_id} | {html.escape(username)}\n"
        f"Размер: <b>{size}</b> см\n"
        f"Чат: {chat_id}"
    )
    if public_label:
        header += f"\n🏷 Публичный лейбл: {html.escape(public_label)}"
    return header


def admin_confirm_reset_player(name: str) -> str:
    return f"♻️ Сбросить игрока «{name}»? Размер и история обнулятся."


def admin_confirm_delete_player(name: str) -> str:
    return f"🗑 Удалить игрока «{name}» из чата? Запись будет удалена безвозвратно."


def admin_confirm_reset_chat(title: str) -> str:
    return f"🧨 Сбросить весь чат «{title}»? Все игроки обнулятся. Действие необратимо."


def admin_health_reform_preview(title: str, result) -> str:
    if result.already_applied:
        return (
            f"🥦 <b>ЗОЖ-реформа · {html.escape(title)}</b>\n\n"
            "Комиссия тут уже всё обрезала. Второй секатор этому чату не положен.\n\n"
            f"Было: <b>{result.total_before}</b> см\n"
            f"Осталось: <b>{result.total_after}</b> см\n"
            f"Срезано: <b>{result.total_cut}</b> см"
        )
    return (
        f"🥦 <b>ЗОЖ-реформа · {html.escape(title)}</b>\n\n"
        "Комиссия объявит всё сверх 50 см не длиной, а запущенным жировым "
        "наростом. Срез пойдёт по ступеням 15% / 25% / 35% и заденет руки, "
        "тело вклада и накопленные проценты.\n\n"
        f"Зажиревших: <b>{result.affected_players}</b>\n"
        f"Общий размер: <b>{result.total_before}</b> → <b>{result.total_after}</b> см\n"
        f"Под секатор: <b>{result.total_cut}</b> см\n\n"
        "Запуск для этого чата возможен только один раз. Начать сушку?"
    )


def health_reform_announcement(result) -> str:
    lines = [
        "🥦 <b>ЗОЖ-РЕФОРМА КОРПОРАЦИИ</b>",
        "",
        "Комиссия наконец выяснила: всё, что у вас торчит сверх санитарной "
        "нормы, — не длина, а запущенный жировой нарост. Лишнее пустили под "
        "корпоративный секатор.",
        "",
        f"Осмотрено зажиревших туш: <b>{result.affected_players}</b>.",
        f"Срезано сала: <b>{result.total_cut}</b> см.",
    ]
    if result.entries:
        lines += ["", "<b>Сильнее всех заплыли:</b>"]
        for i, entry in enumerate(result.entries[:10], 1):
            lines.append(
                f"{i}. {html.escape(entry.name)} — −{entry.cut} см ({entry.before} → {entry.after})"
            )
        hidden = len(result.entries) - 10
        if hidden > 0:
            lines.append(f"…и ещё {hidden} оздоровленных молча собирают обрезки.")
    lines += ["", "Не благодарите. Теперь вы не короткие — вы оздоровленные."]
    return "\n".join(lines)


def admin_health_reform_done(result, announced: bool) -> str:
    suffix = "Акт отправлен в чат." if announced else "Акт в чат не доставлен."
    return f"Сушка окончена: −{result.total_cut} см. {suffix}"


def admin_ask_ban_user_reason(name: str, user_id: int) -> str:
    return f"🚫 За что забанить пользователя {name} (id {user_id})? Выбери причину:"


def admin_ask_ban_chat_reason(title: str) -> str:
    return f"🚫 За что забанить чат «{title}»? Бот перестанет в нём отвечать. Выбери причину:"


def admin_find_found(n: int) -> str:
    return f"Найдено: {n}"


def admin_find_result_line(name: str, size: int, chat_id: int) -> str:
    return f"{name} — {size} см (чат {chat_id})"


def bcast_mode_label(mode: str) -> str:
    return BCAST_MODE_LABELS.get(mode, BCAST_MODE_LABELS["all"])


def admin_bcast_mode_preview(text: str, mode_label: str, target_count: int) -> str:
    return (
        f"📢 Предпросмотр рассылки:\n\n{text}\n\n"
        f"Кому: <b>{html.escape(mode_label)}</b> — получателей: {target_count}.\n"
        "Отправить?"
    )


def admin_bcast_done(sent: int, failed: int) -> str:
    return f"📢 Рассылка завершена. Успешно: {sent}, ошибок: {failed}."


def admin_bcast_history_page(total: int, page: int) -> str:
    return f"🗂 Всего рассылок: {total}. Страница {page + 1}."


def admin_bcast_history_line(
    created_at: int, mode: str, sent: int, failed: int, preview: str
) -> str:
    snippet = html.escape(preview)
    return (
        f"🕒 {fmt_datetime(created_at)} | {bcast_mode_label(mode)} | "
        f"✅ {sent} / ❌ {failed}\n{snippet}"
    )


def admin_chat_label(
    c_type: str, title: str, first_name: str | None, username: str | None, chat_id: int
) -> str:
    """Human label for a chat in lists. For private chats, prefer the owner's
    name/username over the bare id; groups use their title."""
    if c_type == "private":
        if first_name:
            if username:
                return f"{first_name} (@{username})"
            return first_name
        if username:
            return f"@{username}"
        return f"DM {chat_id}"
    return title or str(chat_id)


def admin_global_stats(chats: int, users: int, players: int, total_size: int, active: int) -> str:
    return (
        "📊 <b>Глобальная статистика</b>\n"
        f"Чатов: {chats} (активных: {active})\n"
        f"Пользователей: {users}\n"
        f"Игроков (записей): {players}\n"
        f"Суммарный размер: {total_size} см"
    )


def admin_economy(report) -> str:
    coverage = report.coverage_percent
    coverage_flag = "норма" if coverage >= 100 else "дефицит"
    return (
        "📈 <b>Экономика</b>\n\n"
        f"Игроков: {report.players}\n"
        f"Ликвидно у игроков: {report.liquid} см\n"
        f"За покерными столами: {report.poker_escrow} см\n"
        f"Вклады: {report.deposits} + {report.deposit_interest} см процентов\n"
        f"Созреют: ≤24ч {report.deposits_due_24h} · 1–7д {report.deposits_due_7d} · "
        f"позже {report.deposits_later} см\n"
        f"Долги игроков: {report.loans} см · из них за /dick {report.roll_debts} · "
        f"просрочек: {report.defaults}\n"
        f"К взысканию: уже {report.loans_overdue} · ≤24ч {report.loans_due_24h} · "
        f"1–7д {report.loans_due_7d} · позже {report.loans_later} см\n"
        f"Касса Корпорации: {report.corporation} см\n"
        f"Покрытие вкладов: {coverage:.0f}% ({coverage_flag})\n\n"
        f"Чистое изменение 7 дней: {report.net_delta_7d:+d} см "
        f"({report.active_7d} игроков)\n"
        f"Чистое изменение 30 дней: {report.net_delta_30d:+d} см "
        f"({report.active_30d} игроков)\n"
        f"ПИСЯГО прикрыло: {report.pisyago_covered_7d} см за 7 дней · "
        f"{report.pisyago_covered_30d} см за 30 дней"
    )


# DM notices about bans.
def notify_user_banned(suffix: str) -> str:
    return f"🚫 Вы заблокированы в боте.{suffix}"


def notify_chat_banned(suffix: str) -> str:
    return f"🚫 Этот чат заблокирован администратором.{suffix}"


def ban_reason_suffix(reason: str | None) -> str:
    return f"\nПричина: {reason}" if reason else ""


def ban_until_suffix(date_str: str) -> str:
    return f"\nДо: {date_str}"


# ----------------------------------------------------- admin_actions results ---


def res_size_set(name: str, size: int) -> str:
    return f"Размер {name} установлен на {size} см."


def res_size_add(name: str, new_size: int, delta: int) -> str:
    return f"Размер {name}: {new_size} см ({delta:+d})."


def res_name_set(name: str) -> str:
    return f"Имя изменено на {name}."


def res_public_label_set(label: str | None) -> str:
    return f"Публичный лейбл установлен: {label}." if label else "Публичный лейбл удалён."


def res_unknown_disease(disease_id: str) -> str:
    return f"Неизвестная болезнь: {disease_id}"


def res_disease_given(name: str) -> str:
    return f"Выдана болезнь: {name}."


RES_CURED = "Игрок вылечен."
RES_PLAYER_RESET = "Игрок сброшен (0 см, без болезни)."
RES_PLAYER_NOT_FOUND = "Игрок не найден."
RES_USER_NOT_FOUND = "Пользователь не найден."
RES_PUBLIC_LABEL_INVALID = "Некорректный публичный лейбл."
RES_PLAYER_DELETED = "Игрок удалён из чата."
RES_CANT_BAN_ADMIN = "Нельзя забанить глобального администратора."
RES_CHAT_NOT_FOUND = "Чат не найден."


def res_chat_reset(n: int) -> str:
    return f"Чат сброшен, удалено игроков: {n}."


def res_user_banned(user_id: int, suffix: str) -> str:
    return f"Пользователь {user_id} забанен.{suffix}"


def res_user_unbanned(user_id: int) -> str:
    return f"Пользователь {user_id} разбанен."


def res_chat_banned(chat_id: int) -> str:
    return f"Чат {chat_id} забанен."


# --------------------------------------------------------------------- /bank ---


def _dur(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {mins} мин"
    return f"{mins} мин"


BANK_TITLE = "🏦 <b>Банк «Корпорации»</b>"
BANK_DISABLED = "🏦 Банк тут прикрыли. Иди ной админам, а Корпорации твои сопли до фонаря."
BANK_GROUP_ONLY = (
    "🏦 Банк пашет только в группах — там, где есть у кого отжать. В личке тебя и грабить лень."
)
BANK_NOT_YOURS = "Эта банковская панель не твоя. Открой свою командой /bank."

BTN_BANK_DEPOSIT = "💰 Вклад"
BTN_BANK_LOAN = "🏦 Кредит"
BTN_BANK_SEKASKO = "🩲 СЕКАСКО"
BTN_BANK_CORP = "🏢 Корпорация"
BTN_BANK_REFRESH = "🔄 Обновить"
BTN_BANK_CLOSE = "✖ Закрыть"
BTN_BANK_BACK = "« Назад"
BTN_RULES_RUDE = "📜 Правила по-пацански"
BTN_RULES_STRICT = "🤓 Правила занудные"
BTN_DEP_OPEN = "➕ Положить"
BTN_DEP_WITHDRAW = "➖ Снять"
BTN_LOAN_TAKE = "➕ Взять"
BTN_LOAN_REPAY = "➖ Погасить"
BTN_AMOUNT_CUSTOM = "✏️ Своя сумма"
BTN_AMOUNT_ALL = "Всё"


def bank_screen(s) -> str:
    lines = [BANK_TITLE, "", f"💪 На руках (ликвидно): <b>{s.size}</b>"]
    insurance = s.pisyago
    if insurance.limit <= 0 or insurance.threshold <= 0:
        lines.append("🛡 ПИСЯГО: отключено. Корпорация убрала памперсы даже у нищих.")
    elif insurance.coverage_pct > 0:
        window = (
            f"до {fmt_datetime(insurance.reset_at)}"
            if insurance.reset_at
            else "период начнётся с первой выплаты"
        )
        lines.append(
            f"🛡 ПИСЯГО: <b>{insurance.coverage_pct}%</b> · запас "
            f"<b>{insurance.remaining}/{insurance.limit}</b> см · {window}"
        )
        lines.append(
            f"↳ Учтённые активы: {insurance.assets}/{insurance.threshold} см. "
            "Вклад и покерный стек не спрячешь, хитрожопый актуарий."
        )
    else:
        lines.append(
            f"🛡 ПИСЯГО: не положено — активов {insurance.assets} см при пороге "
            f"{insurance.threshold}. Страховой сосок уже не по размеру."
        )
    if s.deposit:
        d = s.deposit
        status = (
            "🔓 созрел"
            if d.matured
            else f"🔒 до {fmt_datetime(d.matures_at)} ({_dur(d.matures_at - _now_ts())})"
        )
        lines.append(f"💰 Вклад: <b>{d.principal}</b> (+{d.accrued} см) · {status}")
        lines.append(
            f"🛟 Защита тела: база {s.sekasko.base_protected} + "
            f"СЕКАСКО {s.sekasko.sekasko_protected} = "
            f"<b>{s.sekasko.total_protected}</b> см · под риском <b>{s.sekasko.risky}</b>"
        )
    else:
        lines.append("💰 Вклад: голяк. Деньги от тебя шарахаются, нищук.")
        if s.sekasko.active:
            lines.append(
                f"🩲 СЕКАСКО: активно <b>{s.sekasko.active}</b> см — покрытие ждёт будущий вклад."
            )
        else:
            lines.append("🩲 СЕКАСКО: активного покрытия нет.")
    if s.loan:
        ln = s.loan
        flag = (
            "❗️ПРОСРОЧКА"
            if ln.defaulted
            else f"до {fmt_datetime(ln.due_at)} ({_dur(ln.due_at - _now_ts())})"
        )
        lines.append(
            f"🏦 Долг: <b>{ln.debt}</b> ({ln.principal} тело + {ln.interest} проценты) · {flag}"
        )
        if ln.roll_debt:
            lines.append(
                f"↳ Из тела <b>{ln.roll_debt}</b> см — счёт за провальные замеры /dick. "
                "Корпорация даже твой минус превратила в подписку."
            )
    else:
        lines.append("🏦 Долг: чисто. Пока никому не должен, везунчик.")
    lines.append(f"📈 Кредитный рейтинг: +{s.loans_repaid} / −{s.loans_defaulted}")
    if s.next_credit_reward_at > _now_ts():
        lines.append(f"↳ Следующий честный плюс не раньше {fmt_datetime(s.next_credit_reward_at)}")
    lines.append(f"🧾 Доступный кредит: <b>{s.loan_limit}</b>")
    return "\n".join(lines)


def _now_ts() -> int:
    import time as _t

    return int(_t.time())


def bank_dep_screen(s) -> str:
    lines = [BANK_TITLE, "", "💰 <b>Вклад</b>", ""]
    if s.deposit:
        d = s.deposit
        status = (
            "🔓 созрел — снимай без штрафа"
            if d.matured
            else f"🔒 до {fmt_datetime(d.matures_at)} — ещё {_dur(d.matures_at - _now_ts())} под замком"
        )
        lines += [
            f"Тело: <b>{d.principal}</b>",
            f"Накапало: <b>{d.accrued}</b>",
            f"Базовая защита: <b>{s.sekasko.base_protected}</b> см",
            f"Защита СЕКАСКО поверх базы: <b>{s.sekasko.sekasko_protected}</b> см",
            f"Всего защищено тело: <b>{s.sekasko.total_protected}</b> см",
            f"Рисковое тело: <b>{s.sekasko.risky}</b> см",
            status,
        ]
    else:
        lines.append("Вклада нет. Жмёшься, как последний скряга.")
    lines += [
        "",
        f"На руках: {s.size}",
        "",
        f"🛟 Первые {s.sekasko.base_limit} см тела защищены без СЕКАСКО. СЕКАСКО прикрывает тело только сверх этой базы.",
        "Защита действует при случайной конфискации и распиле Корпорации. Начисленные проценты и взыскание просроченного кредита не защищены.",
        "",
        "⚠️ Процент капает только в дни, когда ты тыкаешь /dick, потом быстро дохнет и упирается в потолок Корпорации. Дробная мелочь копится честно, но халявного +1 больше нет.",
    ]
    return "\n".join(lines)


def bank_sekasko_screen(s) -> str:
    policy = s.sekasko
    lines = [BANK_TITLE, "", "🩲 <b>СЕКАСКО вклада</b>", ""]

    if policy.active:
        lines.append(f"Активное покрытие: <b>{policy.active}</b> см")
        if policy.next_expiring >= policy.active:
            lines.append(
                f"Всё активное покрытие закончится {fmt_datetime(policy.next_expires_at)} "
                f"(через {_dur(policy.next_expires_at - _now_ts())})."
            )
        else:
            remaining = policy.active - policy.next_expiring
            lines.append(
                f"Ближайшее уменьшение: −{policy.next_expiring} см "
                f"{fmt_datetime(policy.next_expires_at)} "
                f"(через {_dur(policy.next_expires_at - _now_ts())}); останется {remaining} см."
            )
    else:
        lines.append("Активного покрытия нет.")

    lines.append("")
    if s.deposit:
        lines += [
            f"Тело вклада: <b>{s.deposit.principal}</b> см",
            f"Базовая защита без полиса: <b>{policy.base_protected}</b> см",
            f"СЕКАСКО применено поверх базы: <b>{policy.sekasko_protected}</b> см",
            f"Всего защищено: <b>{policy.total_protected}</b> см",
            f"Рисковое тело: <b>{policy.risky}</b> см",
        ]
        if policy.unused:
            lines.append(
                f"Пока не задействовано: <b>{policy.unused}</b> см — покрытие больше тела вклада."
            )
        lines += [
            "",
            f"База без полиса: до <b>{policy.base_limit}</b> см тела",
            f"Лимит СЕКАСКО сверх базы для этого вклада: <b>{policy.purchase_limit}</b> см",
            f"Свободно в лимите: <b>{policy.limit_available}</b> см",
            f"Можно купить сейчас: <b>{policy.available}</b> см",
        ]
        if policy.available < policy.limit_available:
            lines.append("↳ Сумму ограничивают сантиметры на руках для оплаты премии.")
    else:
        lines += [
            "Покрывать пока нечего: вклада нет.",
            "Уже активное покрытие не пропадает и применится к телу будущего вклада.",
            f"Первые <b>{policy.base_limit}</b> см будущего тела будут защищены без полиса.",
            f"Текущий лимит для выдачи нового покрытия: <b>{policy.configured_limit}</b> см.",
            "Новое покрытие можно купить после открытия вклада.",
        ]

    lines += [
        "",
        f"Премия: <b>{policy.premium_pct}%</b> от выбранного покрытия, округляется вверх; минимум 1 см.",
    ]
    if policy.available:
        lines.append(
            f"Покрытие {policy.available} см сейчас стоит <b>{policy.available_premium}</b> см премии."
        )
    lines += [
        "",
        "⚠️ Сумма в кнопке — это новое покрытие, не цена. Премия указана рядом и списывается с сантиметров на руках.",
        "⚠️ Покрытие не выдаёт сантиметры на руки. База и СЕКАСКО защищают только тело от случайной конфискации и распила Корпорации.",
        "⚠️ Начисленные проценты и взыскание просроченного кредита не защищаются никогда.",
    ]
    return "\n".join(lines)


def bank_loan_screen(s) -> str:
    lines = [BANK_TITLE, "", "🏦 <b>Кредит</b>", ""]
    if s.loan:
        ln = s.loan
        flag = (
            "❗️ПРОСРОЧКА — Корпорация уже точит ножи"
            if ln.defaulted
            else f"вернуть до {fmt_datetime(ln.due_at)} — через {_dur(ln.due_at - _now_ts())}"
        )
        lines += [f"Долг: <b>{ln.debt}</b> ({ln.principal} тело + {ln.interest} проценты)", flag]
        if ln.roll_debt:
            lines.append(f"За обмякшие броски /dick: <b>{ln.roll_debt}</b> см")
    else:
        lines.append("Долгов нет. Пока не влез, терпила.")
    lines += [
        "",
        f"На руках: {s.size}",
        f"Доступно взять: <b>{s.loan_limit}</b>",
        "",
        "⚠️ Не вернёшь в срок — выгрызем с /dick и с побед в дуэлях, а в ЛС прилетит такое письмо, что уши свернутся.",
    ]
    return "\n".join(lines)


def corp_screen(corp) -> str:
    head = "🏢 <b>Корпорация</b>"
    if corp.status == "sanation":
        mood = "🚨 СТАРАЯ САНАЦИЯ. На следующем проходе Корпорация немедленно проведёт распил без отсрочки."
    elif corp.status == "recovery":
        mood = f"🩼 ПОСЛЕ РАСПИЛА. Баланс кассы: <b>{corp.balance}</b> см; банк ползёт из минуса и копит живые деньги."
    else:
        mood = f"💼 Касса пока не сдохла: <b>{corp.balance}</b> см."
    return (
        f"{head}\n\n{mood}\n\n"
        f"• Вклады к возврату: {corp.deposits}\n"
        f"• Неприкосновенный резерв: {corp.reserve_required}\n"
        f"• Свободно для кредитов и раздач: {corp.spendable}\n"
        f"• Резерв СЕКАСКО: {corp.insurance_reserve}\n"
        f"• Напечатано через /dick: {corp.total_emission}\n"
        f"• Списано при распилах: {corp.total_bailin}\n"
        f"• Налогов с дуэлей: {max(0, corp.total_tax - corp.total_poker_rake)}\n"
        f"• Слизано с покерных банков: {corp.total_poker_rake}\n"
        f"• Процентов с кредитов: {corp.total_interest_earned}\n"
        f"• Выплачено по вкладам: {corp.total_interest_paid}\n"
        f"• Штрафов и конфискаций: {corp.total_penalties}"
    )


def bank_enter_amount(action: str) -> str:
    return f"Введи сумму ({action}) числом. Или жми «Отмена»."


def bank_enter_sekasko_amount(available: int, premium_pct: int) -> str:
    return (
        "Введи сумму нового покрытия СЕКАСКО числом. "
        f"Доступно до {available} см; премия {premium_pct}% от покрытия "
        "с округлением вверх (минимум 1 см). Или жми «Отмена»."
    )


# Op result / error notices.
BANK_ERR = {
    "no_size": "💢 Класть нечего, голодранец. Сперва отрасти хоть что-то через /dick.",
    "no_deposit": "💢 Какой вклад? У тебя и в помине ничего нет. Снимать воздух будешь?",
    "no_loan": "💢 Тебе никто и копейки не доверил. Гасить нечего, фантазёр.",
    "loan_exists": "💢 У тебя уже долг на шее болтается. Один хомут на рыло — сперва расплатись.",
    "no_credit": "💢 Кредитный рейтинг — дно из донных. Корпорация в голос ржёт над твоей мордой.",
    "corp_broke": "💢 В кассе Корпорации шаром покати. Раздавать нечего — иди наполняй её дуэлями, а потом приходи клянчить.",
    "loan_denied": "💢 Тебе только что дали от ворот поворот. Не долби в кассу как дятел — посиди в углу, остынь и приходи позже.",
    "bad_amount": "💢 Это не сумма, а каракули. Тыкни нормальное число, грамотей.",
    "corp_frozen": "💢 Касса заморожена и выгребает дефицит. Новые вклады, кредиты и СЕКАСКО закрыты до восстановления.",
    "insurance_limit": "💢 Столько СЕКАСКО не налезет. Страхуй только доступную часть до лимита, математический онанист.",
    "insurance_cash": "💢 На страховую премию не хватает ликвидных сантиметров. Даже трусы в кредит тебе не дают.",
}


def dep_opened(amount: int) -> str:
    return f"💰 Заморозил <b>{amount}</b> во вкладе. Этот кусок спрятался из /top и в дуэль не полезет. Сиди, труси над процентами, жмот."


def dep_withdrawn(amount: int, penalty: int) -> str:
    if penalty > 0:
        return f"➖ Дёрнул раньше срока: на руки <b>{amount}</b>, а <b>{penalty}</b> Корпорация отжала за твоё нетерпение. Будешь знать."
    return f"➖ Забрал со вклада <b>{amount}</b>. Дотерпел до срока — на этот раз без штрафа, везунчик."


def bank_bail_in_notice(
    initiator: str,
    wiped: int,
    payout: int,
    balance: int,
    deficit: int,
) -> str:
    safe_initiator = html.escape(initiator)
    return (
        f"🏆 <b>{safe_initiator}</b> первым добрался до кассы и устроил Корпорации распил!\n\n"
        "Аплодисменты финансовому чутью — защищённая часть ушла победителю первой.\n"
        f"С вкладов суммарно списано: <b>{wiped}</b> см.\n"
        f"Защищённая выплата по запросу: <b>{payout}</b> см.\n"
        f"Баланс кассы после выплаты: <b>{balance}</b> см.\n"
        f"Дефицит финансирования: <b>{deficit}</b> см.\n\n"
        "Чужие остатки и персональные балансы не публикуются."
    )


def bank_legacy_bail_in_notice(
    wiped: int,
    protected_claims: int,
    balance: int,
    deficit: int,
) -> str:
    return (
        "🚨 <b>Старая санация завершена автоматически</b>\n\n"
        f"С вкладов суммарно списано: <b>{wiped}</b> см.\n"
        f"Защищённые требования сохранены: <b>{protected_claims}</b> см.\n"
        f"Баланс кассы: <b>{balance}</b> см.\n"
        f"Дефицит финансирования: <b>{deficit}</b> см.\n\n"
        "Персональные остатки вкладчиков не публикуются."
    )


def loan_taken(amount: int, due_at: int) -> str:
    return f"🏦 На, держи <b>{amount}</b> в долг. Вернуть до {fmt_datetime(due_at)}. Кинешь — потом не вой, сам напросился."


def loan_repaid(amount: int, cleared: bool) -> str:
    if cleared:
        return f"✅ Закрыл долг под ноль (−{amount}). Рейтинг вырастет только если кредит был достаточно крупным, старым и не накручен твоими потными пальцами."
    return f"➖ Кинул <b>{amount}</b> в счёт долга. Остальное капает, не расслабляй булки."


def dick_deposit_interest(amount: int) -> str:
    return f"💰 Вклад капнул +{amount} — за то, что сегодня не сдох и доковылял до /dick."


def sekasko_bought(amount: int, premium: int) -> str:
    return f"🩲 СЕКАСКО натянуто на <b>{amount}</b> см. Премия <b>{premium}</b> см списана отдельно — безопасность твоей мошонки бесплатной не бывает."


def dick_garnished(amount: int) -> str:
    return f"🩸 Коллекторы выгрызли {amount} с твоего прироста в счёт долга. Не нравится — гаси."


def duel_garnished(amount: int) -> str:
    return f"🩸 С выигрыша отжали {amount} за твою просрочку. Победил, а навар уплыл — поделом."


def collector_reminder(debt: int, overdue_for: int) -> str:
    return (
        f"📨 <b>Письмо от Корпорации</b>\n\n"
        f"Слышь, должник. За тобой <b>{debt}</b>, и просрочка уже {_dur(overdue_for)}. "
        f"Долг мы заморозили, зато теперь тихонько режем твои /dick и победы. "
        f"Тащи бабки через /bank, пока мы добрые — а добрые мы недолго."
    )


def profile_bank(s) -> str | None:
    """One-line bank summary for /me. None when the player has no bank activity."""
    parts: list[str] = []
    if s.deposit:
        parts.append(f"вклад {s.deposit.principal}(+{s.deposit.accrued})")
    if s.loan:
        flag = "❗просрочка" if s.loan.defaulted else "в срок"
        roll = f", /dick: {s.loan.roll_debt}" if s.loan.roll_debt else ""
        parts.append(f"долг {s.loan.debt} ({flag}{roll})")
    if s.pisyago.coverage_pct > 0:
        parts.append(
            f"ПИСЯГО {s.pisyago.coverage_pct}% ({s.pisyago.remaining}/{s.pisyago.limit} см)"
        )
    if not parts and s.loans_repaid == 0 and s.loans_defaulted == 0:
        return None
    rating = f"рейтинг +{s.loans_repaid}/−{s.loans_defaulted}"
    body = " · ".join([*parts, rating]) if parts else rating
    return f"🏦 Банк: {body}"


ADMIN_GSET_BANK_TITLE = "🏦 <b>Настройки банка</b>\nСтавки и сроки едины для всех чатов."
BTN_GSET_BANK = "🏦 Настройки банка"
ADMIN_GSET_INSURANCE_TITLE = (
    "🛡 <b>Настройки ПИСЯГО</b>\n"
    "Страховка смягчает отрицательный /dick игрокам с активами ниже порога."
)
BTN_GSET_INSURANCE = "🛡 Настройки ПИСЯГО"
ADMIN_GSET_CORP_TITLE = (
    "🏢 <b>Локальные Корпорации и СЕКАСКО</b>\nНормы едины, кассы у чатов отдельные."
)
BTN_GSET_CORP = "🏢 Корпорации и СЕКАСКО"


def res_chat_unbanned(chat_id: int) -> str:
    return f"Чат {chat_id} разбанен."
