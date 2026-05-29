import asyncio
import random
import secrets
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from models.disease import (
    apply_duel_mod,
    check_expire,
    disease_tag,
    try_infect,
)
from repositories.players import get_chat_lock, get_storage, save_storage

router = Router()

DUEL_TIMEOUT = 60
DEFAULT_STAKE = 5

_duels: dict[str, dict] = {}

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

REACTION_TIERS = [
    (0, 3, -0.15, "МГНОВЕННАЯ реакция! {loser} почти увернулся... но не совсем."),
    (3, 8, -0.07, "Быстрая реакция, {loser} пытался уклониться."),
    (8, 20, 0.00, "Обычная реакция. Ни fast, ни slow."),
    (20, 40, 0.05, "Слегка замешкался... {loser} явно отвлекся на котиков."),
    (40, 999, 0.12, "ОЧЕНЬ долго думал... {loser} залип в телефоне и поплатился."),
]


def _mention(user_id: int, name: str) -> str:
    return f"<a href=\"tg://user?id={user_id}\">{name}</a>"


async def _safe_edit(callback: CallbackQuery, text: str, **kwargs) -> None:
    """Edit the callback message if it is still available, otherwise answer."""
    if callback.message is not None:
        await callback.message.edit_text(text, **kwargs)
    else:
        await callback.answer(text, show_alert=True)


async def _expire_duel(bot: Bot, chat_id: int, message_id: int, token: str) -> None:
    await asyncio.sleep(DUEL_TIMEOUT)
    if token not in _duels:
        return
    _duels.pop(token, None)
    try:
        await bot.edit_message_text(
            "Вызов истёк. Никто так и не откликнулся.",
            chat_id=chat_id,
            message_id=message_id,
        )
    except Exception:
        pass


def _gen_token() -> str:
    return secrets.token_hex(4)


def _pop_duel(token: str) -> dict | None:
    return _duels.pop(token, None)


SIZE_WEIGHT = 0.2


def _calc_base_chance(attacker_size: int, defender_size: int) -> float:
    total = attacker_size + defender_size
    if total == 0:
        return 0.5
    return 0.5 + (attacker_size - defender_size) / total * SIZE_WEIGHT


def _resolve_fight(
    attacker_size: int,
    defender_size: int,
    reaction_sec: float,
    attacker_name: str,
    defender_name: str,
    attacker_raw_name: str,
    defender_raw_name: str,
    base_chance: float,
) -> tuple[bool, str, str, str, float]:
    reaction_mod = 0.0
    reaction_comment = "Обычная реакция. Ничего особенного."

    for lo, hi, mod, comment in REACTION_TIERS:
        if lo <= reaction_sec < hi:
            reaction_mod = mod
            reaction_comment = comment.format(loser=defender_name)
            break

    luck = random.uniform(-0.1, 0.1)
    chance = max(0.05, min(0.95, base_chance + reaction_mod + luck))
    winner_is_attacker = random.random() < chance

    if winner_is_attacker:
        winner = attacker_name
        loser = defender_name
        loser_raw = defender_raw_name
    else:
        winner = defender_name
        loser = attacker_name
        loser_raw = attacker_raw_name

    victory = random.choice(VICTORY_LINES).format(winner=winner, loser=loser)
    technique = random.choice(TECHNIQUE_LINES).format(loser_name=loser_raw)

    return winner_is_attacker, victory, technique, reaction_comment, chance


def _build_result_message(
    victory_line: str,
    technique_line: str,
    steal_line: str,
    attacker_name: str,
    defender_name: str,
    attacker_was: int,
    attacker_now: int,
    defender_was: int,
    defender_now: int,
    loser_name: str,
    stake: int,
    winner_profit: int,
    corp_tax: int,
    base_chance: float,
    final_chance: float,
    reaction_comment: str,
    corp_line: str,
    attacker_tag: str,
    defender_tag: str,
    disease_note: str,
    infection_msg: str,
) -> str:
    result = (
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
    if disease_note:
        result += f"\n{disease_note}"
    result += f"\n{reaction_comment}\n\n{corp_line}"
    if infection_msg:
        result += f"\n\n{infection_msg}"
    return result


@router.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject, bot: Bot) -> None:
    user = message.from_user
    if not user:
        return

    chat_id = message.chat.id
    storage = await get_storage(chat_id)

    a_str = str(user.id)
    if a_str not in storage:
        await message.answer(f"Сначала измерь письку командой /dick, {_mention(user.id, user.first_name)}!")
        return

    attacker_size = storage[a_str]["size"]
    if attacker_size <= 0:
        await message.answer("Твоя писька размером 0 см. Дуэль невозможна. Попробуй /dick.")
        return

    args = command.args
    try:
        stake = int(args.strip()) if args and args.strip().isdigit() else DEFAULT_STAKE
    except ValueError:
        stake = DEFAULT_STAKE

    stake = max(1, min(stake, attacker_size))

    reply = message.reply_to_message
    is_open = not reply or not reply.from_user

    if is_open:
        defender_name = None
        defender_id = 0

        text = (
            f"{_mention(user.id, user.first_name)} бросает ОТКРЫТЫЙ ВЫЗОВ!\n"
            f"Первый смельчак, нажавший кнопку — тот и соперник.\n\n"
            f"Ставка: {stake} см с каждого.\n"
            f"{attacker_size} см vs ??? см (шансы: ? / ?)\n\n"
            f"На раздумья {DUEL_TIMEOUT} секунд."
        )
    else:
        defender_id = reply.from_user.id
        defender_name = reply.from_user.first_name

        if user.id == defender_id:
            await message.answer("Нельзя вызвать на дуэль самого себя. Это было бы странно.")
            return

        d_str = str(defender_id)
        if d_str not in storage:
            await message.answer(f"Сначала измерь письку командой /dick, {_mention(defender_id, defender_name)}!")
            return

        defender_size = storage[d_str]["size"]
        if defender_size <= 0:
            await message.answer(f"У {_mention(defender_id, defender_name)} писька 0 см. Дуэль невозможна.")
            return

        stake = min(stake, defender_size)
        base_chance = _calc_base_chance(attacker_size, defender_size)

        text = (
            f"{_mention(user.id, user.first_name)} вызывает {_mention(defender_id, defender_name)} на пиписечную дуэль!\n\n"
            f"Ставка: {stake} см с каждого.\n"
            f"{attacker_size} см vs {defender_size} см (шанс: {base_chance:.0%} / {1 - base_chance:.0%})\n\n"
            f"У {defender_name} есть {DUEL_TIMEOUT} секунд чтобы принять вызов.\n"
            f"Реакция повлияет на исход!"
        )

    challenge_ts = time.time()
    token = _gen_token()
    _duels[token] = {
        "attacker_id": user.id,
        "defender_id": defender_id,
        "chat_id": chat_id,
        "stake": stake,
        "challenge_ts": challenge_ts,
    }
    callback_data = f"duel:{token}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="-- ПРИНЯТЬ ВЫЗОВ --", callback_data=callback_data)]
        ]
    )

    sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    _duels[token]["message_id"] = sent.message_id
    asyncio.create_task(_expire_duel(bot, chat_id, sent.message_id, token))


@router.callback_query(F.data.startswith("duel:"))
async def on_duel_accept(callback: CallbackQuery) -> None:
    token = callback.data.split("duel:")[1]
    data = _pop_duel(token)

    if data is None:
        await _safe_edit(callback, "Вызов уже недействителен.")
        await callback.answer()
        return

    is_open = data["defender_id"] == 0

    if not is_open and callback.from_user.id != data["defender_id"]:
        await callback.answer("Этот вызов не тебе!", show_alert=True)
        _duels[token] = data
        return

    if callback.from_user.id == data["attacker_id"]:
        await callback.answer("Нельзя принять собственный вызов.", show_alert=True)
        _duels[token] = data
        return

    now = time.time()
    elapsed = now - data["challenge_ts"]
    if elapsed > DUEL_TIMEOUT:
        await _safe_edit(callback, "Вызов просрочен. Дуэль отменена.")
        await callback.answer()
        return

    chat_id = data["chat_id"]
    async with get_chat_lock(chat_id):
        storage = await get_storage(chat_id)

        a_str = str(data["attacker_id"])
        if is_open:
            data["defender_id"] = callback.from_user.id
            d_str = str(data["defender_id"])

            if d_str not in storage:
                await callback.answer("Сначала измерь письку командой /dick!", show_alert=True)
                _duels[token] = data
                return

            defender_size = storage[d_str]["size"]
            if defender_size <= 0:
                await callback.answer("Твоя писька 0 см. Дуэль невозможна.", show_alert=True)
                _duels[token] = data
                return

            stake = min(data["stake"], defender_size)
            data["stake"] = stake
        else:
            d_str = str(data["defender_id"])

        stake = data["stake"]
        attacker = storage[a_str]
        defender = storage[d_str]

        for p in (attacker, defender):
            check_expire(p)

        attacker_size = attacker["size"]
        defender_size = defender["size"]
        base_chance = _calc_base_chance(attacker_size, defender_size)

        disease_note_parts = []
        adjusted = apply_duel_mod(defender, base_chance)
        if adjusted != base_chance and defender.get("disease"):
            dtag = defender.get("disease", {}).get("id", "")
            from models.disease import DISEASE_BY_ID
            d = DISEASE_BY_ID.get(dtag)
            if d:
                disease_note_parts.append(f"{defender['name']}: {d.name} даёт {d.duel_mod:+.0%} к шансу атакующего")
        base_chance = adjusted

        winner_is_attacker, victory_line, technique_line, reaction_comment, final_chance = _resolve_fight(
            attacker_size, defender_size, elapsed,
            _mention(data["attacker_id"], attacker["name"]),
            _mention(data["defender_id"], defender["name"]),
            attacker["name"],
            defender["name"],
            base_chance,
        )

        winner_profit = max(1, int(stake * 0.5))
        corp_tax = stake - winner_profit

        if winner_is_attacker:
            loser_key = d_str
            winner_key = a_str
        else:
            loser_key = a_str
            winner_key = d_str

        loser = storage[loser_key]
        winner = storage[winner_key]

        loser["size"] = max(0, loser["size"] - stake)
        winner["size"] += winner_profit
        storage[loser_key] = loser
        storage[winner_key] = winner

        infection_msg = try_infect(winner, loser) or ""
        if infection_msg:
            infection_msg = f"{loser['name']}: {infection_msg}"

        steal_line = random.choice(STEAL_LINES).format(stolen=stake, loser=loser["name"])
        corp_line = random.choice(CORP_LINES).format(tax=corp_tax)

        attacker_tag = disease_tag(attacker)
        defender_tag = disease_tag(defender)
        disease_note = "\n".join(disease_note_parts)

        result = _build_result_message(
            victory_line, technique_line, steal_line,
            attacker["name"], defender["name"],
            attacker_size, storage[a_str]["size"],
            defender_size, storage[d_str]["size"],
            loser["name"], stake, winner_profit, corp_tax,
            base_chance, final_chance,
            reaction_comment, corp_line,
            attacker_tag, defender_tag, disease_note, infection_msg,
        )

        await save_storage(chat_id, storage)

    await _safe_edit(callback, result, parse_mode="HTML")
    await callback.answer()
