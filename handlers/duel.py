import asyncio
import html
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
from handlers.replies import reply_target
from repositories import events as E
from repositories.players import get_chat_lock, get_storage, save_storage
from services import cooldown
from services.global_settings import get_config_sync
from services.settings import get_effective
import texts
from texts import (
    CORP_LINES,
    REACTION_TIERS,
    STEAL_LINES,
    TECHNIQUE_LINES,
    VICTORY_LINES,
)

router = Router()

_duels: dict[str, dict] = {}
# Strong refs to expiry tasks so they aren't garbage-collected mid-flight.
_expire_tasks: set[asyncio.Task] = set()


def _mention(user_id: int, name: str) -> str:
    return f"<a href=\"tg://user?id={user_id}\">{html.escape(name)}</a>"


async def _safe_edit(callback: CallbackQuery, text: str, **kwargs) -> None:
    """Edit the callback message if it is still available, otherwise answer."""
    if callback.message is not None:
        await callback.message.edit_text(text, **kwargs)
    else:
        await callback.answer(text, show_alert=True)


async def _expire_duel(
    bot: Bot, chat_id: int, message_id: int, token: str, timeout: int
) -> None:
    await asyncio.sleep(timeout)
    if token not in _duels:
        return
    _duels.pop(token, None)
    try:
        await bot.edit_message_text(
            texts.DUEL_EXPIRED_NOBODY,
            chat_id=chat_id,
            message_id=message_id,
        )
    except Exception:
        pass


def _gen_token() -> str:
    return secrets.token_hex(4)


def _calc_base_chance(attacker_size: int, defender_size: int) -> float:
    total = attacker_size + defender_size
    if total == 0:
        return 0.5
    weight = get_config_sync().size_weight
    return 0.5 + (attacker_size - defender_size) / total * weight


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
    reaction_comment = texts.DUEL_DEFAULT_REACTION

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
    result = texts.duel_result(
        victory_line,
        technique_line,
        steal_line,
        winner_profit,
        corp_tax,
        attacker_name,
        attacker_was,
        attacker_now,
        attacker_tag,
        defender_name,
        defender_was,
        defender_now,
        defender_tag,
        base_chance,
        final_chance,
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

    # Anti-flood: ignore rapid repeat invocations silently.
    if not cooldown.check_and_touch(chat_id, user.id, "duel", get_config_sync().cd_duel):
        return

    eff = await get_effective(chat_id)
    duel_timeout = eff.duel_timeout
    default_stake = eff.duel_stake_default

    # Cap simultaneous pending challenges from this user in this chat.
    pending = sum(
        1
        for d in _duels.values()
        if d["chat_id"] == chat_id and d["attacker_id"] == user.id
    )
    if pending >= get_config_sync().max_pending_duels:
        await message.answer(texts.DUEL_TOO_MANY)
        return

    storage = await get_storage(chat_id)

    a_str = str(user.id)
    if a_str not in storage:
        await message.answer(texts.duel_measure_first(_mention(user.id, user.first_name)))
        return

    if storage[a_str].get("chat_banned"):
        if cooldown.check_and_touch(
            chat_id, user.id, "chat_ban_notice", get_config_sync().cd_chat_ban_notice
        ):
            await message.answer(texts.LOCAL_BANNED)
        return

    attacker_size = storage[a_str]["size"]
    if attacker_size <= 0:
        await message.answer(texts.duel_zero_size(_mention(user.id, user.first_name)))
        return

    args = command.args
    try:
        stake = int(args.strip()) if args and args.strip().isdigit() else default_stake
    except ValueError:
        stake = default_stake

    stake = max(1, min(stake, attacker_size))

    target = reply_target(message)
    is_open = target is None

    if is_open:
        defender_name = None
        defender_id = 0

        text = texts.duel_open_challenge(
            _mention(user.id, user.first_name), stake, attacker_size, duel_timeout
        )
    else:
        defender_id = target.id
        defender_name = target.first_name

        if user.id == defender_id:
            await message.answer(texts.DUEL_SELF)
            return

        d_str = str(defender_id)
        if d_str not in storage:
            await message.answer(
                texts.duel_target_measure_first(_mention(defender_id, defender_name))
            )
            return

        defender_size = storage[d_str]["size"]
        if defender_size <= 0:
            await message.answer(texts.duel_target_zero(_mention(defender_id, defender_name)))
            return

        stake = min(stake, defender_size)
        base_chance = _calc_base_chance(attacker_size, defender_size)

        text = texts.duel_directed_challenge(
            _mention(user.id, user.first_name),
            _mention(defender_id, defender_name),
            defender_name,
            stake,
            attacker_size,
            defender_size,
            base_chance,
            duel_timeout,
        )

    challenge_ts = time.time()
    token = _gen_token()
    _duels[token] = {
        "attacker_id": user.id,
        "defender_id": defender_id,
        "chat_id": chat_id,
        "stake": stake,
        "challenge_ts": challenge_ts,
        "timeout": duel_timeout,
    }
    callback_data = f"duel:{token}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.DUEL_ACCEPT_BUTTON, callback_data=callback_data)]
        ]
    )

    sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    _duels[token]["message_id"] = sent.message_id
    task = asyncio.create_task(
        _expire_duel(bot, chat_id, sent.message_id, token, duel_timeout)
    )
    _expire_tasks.add(task)
    task.add_done_callback(_expire_tasks.discard)


@router.callback_query(F.data.startswith("duel:"))
async def on_duel_accept(callback: CallbackQuery) -> None:
    token = callback.data.split("duel:")[1]
    peek = _duels.get(token)

    if peek is None:
        await _safe_edit(callback, texts.DUEL_INVALID)
        await callback.answer()
        return

    # Acquire the per-chat lock BEFORE inspecting/mutating the duel so two
    # concurrent "accept" clicks cannot both pass the checks (no TOCTOU). The
    # token is only removed once the fight actually commits; rejections leave
    # the duel active for a valid retry (no pop/re-insert dance).
    chat_id = peek["chat_id"]
    async with get_chat_lock(chat_id):
        data = _duels.get(token)
        if data is None:
            await _safe_edit(callback, texts.DUEL_INVALID)
            await callback.answer()
            return

        is_open = data["defender_id"] == 0

        if not is_open and callback.from_user.id != data["defender_id"]:
            await callback.answer(texts.DUEL_NOT_YOURS, show_alert=True)
            return

        if callback.from_user.id == data["attacker_id"]:
            await callback.answer(texts.DUEL_OWN, show_alert=True)
            return

        now = time.time()
        elapsed = now - data["challenge_ts"]
        if elapsed > data["timeout"]:
            _duels.pop(token, None)
            await _safe_edit(callback, texts.DUEL_TIMED_OUT)
            await callback.answer()
            return

        storage = await get_storage(chat_id)

        a_str = str(data["attacker_id"])
        if is_open:
            data["defender_id"] = callback.from_user.id
            d_str = str(data["defender_id"])

            if d_str not in storage:
                data["defender_id"] = 0  # keep the open duel claimable by others
                await callback.answer(texts.DUEL_ACCEPT_MEASURE_FIRST, show_alert=True)
                return

            if storage[d_str].get("chat_banned"):
                data["defender_id"] = 0  # keep the open duel claimable by others
                await callback.answer(texts.LOCAL_BANNED, show_alert=True)
                return

            defender_size = storage[d_str]["size"]
            if defender_size <= 0:
                data["defender_id"] = 0  # keep the open duel claimable by others
                await callback.answer(texts.DUEL_ACCEPT_ZERO, show_alert=True)
                return

            stake = min(data["stake"], defender_size)
            data["stake"] = stake
        else:
            d_str = str(data["defender_id"])

        # Fight is committed — remove the token so it can't be replayed.
        _duels.pop(token, None)
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
                disease_note_parts.append(texts.duel_disease_note(d.name, d.duel_mod, defender['name']))
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

        ts = int(now)
        if winner_is_attacker:
            winner_id, loser_id = data["attacker_id"], data["defender_id"]
            winner_before, loser_before = attacker_size, defender_size
        else:
            winner_id, loser_id = data["defender_id"], data["attacker_id"]
            winner_before, loser_before = defender_size, attacker_size
        winner_after = storage[winner_key]["size"]
        loser_after = storage[loser_key]["size"]

        await E.ensure_baseline(chat_id, winner_id, winner_before, created_at=ts)
        await E.ensure_baseline(chat_id, loser_id, loser_before, created_at=ts)
        await E.log_event(
            chat_id, winner_id, E.DUEL,
            delta=winner_after - winner_before,
            size_after=winner_after,
            meta={
                "won": True, "opponent_id": loser_id, "stake": stake,
                "profit": winner_profit, "tax": corp_tax,
            },
            created_at=ts,
        )
        await E.log_event(
            chat_id, loser_id, E.DUEL,
            delta=loser_after - loser_before,
            size_after=loser_after,
            meta={"won": False, "opponent_id": winner_id, "stake": stake},
            created_at=ts,
        )
        if infection_msg:
            await E.log_event(
                chat_id, loser_id, E.INFECTION,
                size_after=loser_after,
                meta={"disease_id": loser.get("disease", {}).get("id")},
                created_at=ts,
            )

    await _safe_edit(callback, result, parse_mode="HTML")
    await callback.answer()
