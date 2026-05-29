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

# Max lengths for free-text admin input (truncated before storing/displaying).
MAX_NAME_LEN = 64
MAX_QUERY_LEN = 64
MAX_BAN_REASON_LEN = 200


def fmt_datetime(ts: int) -> str:
    tz = ZoneInfo(key=os.environ.get("TZ", "Europe/Moscow"))
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y %H:%M")


def fmt_date(ts: int) -> str:
    tz = ZoneInfo(key=os.environ.get("TZ", "Europe/Moscow"))
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m.%Y")


# --------------------------------------------------------------------- /dick ---

def dick_already_today(mention: str, size: int, rank: int, remaining: str) -> str:
    return (
        f"{mention}, твой {DICK} равен {size} см.\n"
        f"Ты занимаешь {rank} место в топе.\n"
        f"Попробуй через {remaining}"
    )


def dick_grew(delta: int) -> str:
    return f"вырос на {delta} см"


def dick_shrank(delta: int) -> str:
    return f"уменьшился на {abs(delta)} см"


def dick_result(mention: str, change_text: str, size: int, rank: int, remaining: str) -> str:
    return (
        f"{mention}, твой {DICK} {change_text}.\n"
        f"Теперь он равен {size} см.\n"
        f"Ты занимаешь {rank} место в топе.\n"
        f"Следующая попытка завтра, через {remaining}!"
    )


# ---------------------------------------------------------------------- /top ---

TOP_EMPTY = "😥 Пока нет игроков\nПрисоединяйся — напиши /dick"
TOP_HEADER = "🏆 Топ 10:\n"


def top_line(rank: int, name: str, tag: str, size: int) -> str:
    return f"{rank}. <b>{html.escape(name)}{tag}</b> ({size} см)"


# ----------------------------------------------------------------------- /me ---

def profile_not_played(mention: str) -> str:
    return f"{mention} ещё не играл. Измерь {DICK} командой /dick!"


def profile_header(name: str) -> str:
    return f"Профиль {name}"


def profile_size(size: int, rank: int) -> str:
    return f"Размер: {size} см ({rank} место в топе)"


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


def profile_current_disease(name: str) -> str:
    return f"Сейчас болеет: {name}"


def profile_size_timeline(spark: str) -> str:
    return f"Размер во времени: {spark}"


def profile_deltas(n: int, spark: str) -> str:
    return f"Изменения размера ({n} дн.): {spark}"


# ----------------------------------------------------- /me global profile ---

GLOBAL_BUTTON = "🌐 Глобальный профиль"
GLOBAL_EMPTY = "Ты ещё нигде не играл. Измерь {DICK} командой /dick в любом чате!".format(DICK=DICK)


def global_header(name: str) -> str:
    return f"🌐 Глобальный профиль {name}"


GLOBAL_CHATS_HEADER = "Чаты:"
GLOBAL_NO_CHATS = "Пока ни в одном чате нет накоплений."


def global_chat_line(title: str, size: int, rank: int) -> str:
    return f"• {title}: {size} см ({rank} место)"


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
    "/duel [ставка] — вызвать на дуэль (ответом на сообщение)\n"
    "/me — твой профиль и статистика\n"
    "/top — топ-10 по размеру\n"
    "/ping — ping-pong\n"
    "/setbcast — (админам, внутри темы) выбрать тему для рассылок\n"
    "/unsetbcast — (админам) сбросить тему рассылок"
)

HELP_ADMIN = "\n\n/admin — панель глобального администратора (в личке с ботом)"


# ------------------------------------------------------ /setbcast /unsetbcast ---

BCAST_NOT_ADMIN = "Менять тему рассылки может только администратор чата."
BCAST_NEED_TOPIC = (
    "Выполни эту команду внутри нужной темы форума — именно туда будут "
    "приходить рассылки."
)
BCAST_SET = "✅ Эта тема выбрана для рассылок."
BCAST_CLEARED = "Тема рассылки сброшена. Теперь будет использоваться самая активная тема."
BCAST_NOT_SET = "Тема рассылки и так не была задана."


# ------------------------------------------------------------------ registry ---

DM_GATE_BUTTON = "✍️ Написать боту"
DM_GATE = (
    "👋 Чтобы пользоваться ботом, сначала напиши ему в личку "
    "(кнопка ниже, затем /start)."
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
BTN_BCAST = "📢 Рассылка"
BTN_HOME = "🏠 Меню"
BTN_PREV = "« Назад"
BTN_NEXT = "Вперёд »"
BTN_BACK_LIST = "« К списку"
BTN_BACK_CHAT = "« К чату"
BTN_BACK = "« Назад"
BTN_YES = "✅ Да"
BTN_CANCEL = "❌ Отмена"
BTN_OWN_REASON = "✏️ Своя причина"

BTN_RESET_CHAT = "🧨 Сброс чата"
BTN_UNBAN = "✅ Разбан"
BTN_BAN_CHAT = "🚫 Бан чата"
BTN_SET_SIZE = "🔢 Задать размер"
BTN_SET_NAME = "✏️ Имя"
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
ADMIN_ENTER_BAN_REASON = "Введи причину бана:"
ADMIN_ENTER_BAN_CHAT_REASON = "Введи причину бана чата:"
ADMIN_REASON_EMPTY = "Пустая причина. Отменено."
ADMIN_ENTER_FIND = "Введи ID игрока или часть имени:"
ADMIN_FIND_EMPTY = "Пустой запрос. Отменено."
ADMIN_FIND_NONE = "Ничего не найдено."
ADMIN_ENTER_BCAST = "Введи текст рассылки (HTML). Будет отправлено во все чаты:"
ADMIN_BCAST_EMPTY = "Пустой текст. Отменено."
ADMIN_BCAST_LOST = "Текст рассылки потерян. Отменено."
ADMIN_BCAST_STARTED = "📢 Рассылка началась…"
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


def admin_player_header(name: str, tag: str, user_id: int, username: str, size: int, chat_id: int) -> str:
    return (
        f"👤 <b>{html.escape(name)}</b>{tag}\n"
        f"id: {user_id} | {html.escape(username)}\n"
        f"Размер: <b>{size}</b> см\n"
        f"Чат: {chat_id}"
    )


def admin_confirm_reset_player(name: str) -> str:
    return f"♻️ Сбросить игрока «{name}»? Размер и история обнулятся."


def admin_confirm_delete_player(name: str) -> str:
    return f"🗑 Удалить игрока «{name}» из чата? Запись будет удалена безвозвратно."


def admin_confirm_reset_chat(title: str) -> str:
    return f"🧨 Сбросить весь чат «{title}»? Все игроки обнулятся. Действие необратимо."


def admin_ask_ban_user_reason(name: str, user_id: int) -> str:
    return f"🚫 За что забанить пользователя {name} (id {user_id})? Выбери причину:"


def admin_ask_ban_chat_reason(title: str) -> str:
    return f"🚫 За что забанить чат «{title}»? Бот перестанет в нём отвечать. Выбери причину:"


def admin_find_found(n: int) -> str:
    return f"Найдено: {n}"


def admin_find_result_line(name: str, size: int, chat_id: int) -> str:
    return f"{name} — {size} см (чат {chat_id})"


def admin_bcast_preview(text: str) -> str:
    return f"📢 Предпросмотр рассылки:\n\n{text}\n\nОтправить во все чаты?"


def admin_bcast_done(sent: int, failed: int) -> str:
    return f"📢 Рассылка завершена. Успешно: {sent}, ошибок: {failed}."


def admin_global_stats(chats: int, users: int, players: int, total_size: int) -> str:
    return (
        "📊 <b>Глобальная статистика</b>\n"
        f"Чатов: {chats}\n"
        f"Пользователей: {users}\n"
        f"Игроков (записей): {players}\n"
        f"Суммарный размер: {total_size} см"
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


def res_unknown_disease(disease_id: str) -> str:
    return f"Неизвестная болезнь: {disease_id}"


def res_disease_given(name: str) -> str:
    return f"Выдана болезнь: {name}."


RES_CURED = "Игрок вылечен."
RES_PLAYER_RESET = "Игрок сброшен (0 см, без болезни)."
RES_PLAYER_NOT_FOUND = "Игрок не найден."
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


def res_chat_unbanned(chat_id: int) -> str:
    return f"Чат {chat_id} разбанен."
