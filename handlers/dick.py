import html
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import texts
from models.disease import (
    apply_growth_mod,
    check_expire,
    disease_tag,
    roll_infection,
)
from repositories import events as E
from repositories.players import (
    PlayerDict,
    Storage,
    get_chat_lock,
    get_storage,
    save_storage,
)
from services import cooldown
from services.game import roll_delta
from services.global_settings import get_config_sync
from services.settings import get_effective, resolve_tz

router = Router()


def _rank(storage: Storage, user_id: int) -> int:
    players = sorted(storage.items(), key=lambda x: x[1]["size"], reverse=True)
    for i, (uid, _) in enumerate(players):
        if int(uid) == user_id:
            return i + 1
    return len(players) + 1


def _time_until_midnight(now: datetime) -> str:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = tomorrow - now
    total = int(delta.total_seconds())
    hours = total // 3600
    minutes = (total % 3600) // 60
    return f"{hours} ч {minutes} мин"


def _mention(user_id: int, name: str) -> str:
    return f"<a href=\"tg://user?id={user_id}\">{html.escape(name)}</a>"


@router.message(Command("dick"))
async def cmd_dick(message: Message) -> None:
    user = message.from_user
    if not user:
        return

    user_id = user.id
    chat_id = message.chat.id
    eff = await get_effective(chat_id)
    tz = await resolve_tz(chat_id)
    now = datetime.now(tz)
    uid_str = str(user_id)

    async with get_chat_lock(chat_id):
        storage = await get_storage(chat_id)

        if uid_str in storage and storage[uid_str].get("chat_banned"):
            if cooldown.check_and_touch(
                chat_id, user_id, "chat_ban_notice", get_config_sync().cd_chat_ban_notice
            ):
                await message.answer(texts.LOCAL_BANNED)
            return

        changed = False
        for pid in list(storage.keys()):
            if check_expire(storage[pid]):
                changed = True

        if uid_str in storage:
            owner = storage[uid_str]
            last_dt = datetime.fromtimestamp(owner["last"], tz=tz)
            if last_dt.date() == now.date():
                if changed:
                    await save_storage(chat_id, storage)
                # Repeated /dick after today's play: answer at most once per
                # minute so the command can't be used to flood the chat.
                if not cooldown.check_and_touch(
                    chat_id, user_id, "dick_repeat", get_config_sync().cd_dick_repeat
                ):
                    return
                mention = _mention(user_id, user.first_name)
                rank = _rank(storage, user_id)
                remaining = _time_until_midnight(now)
                dtag = disease_tag(owner)
                text = texts.dick_already_today(mention, owner["size"], rank, remaining)
                if dtag:
                    text += f"\n{dtag}"
                await message.answer(text, parse_mode="HTML")
                return

        rolled = roll_delta()

        player: PlayerDict = storage.get(uid_str, {"name": user.first_name, "size": 0, "last": 0})

        before = player["size"]
        delta = apply_growth_mod(player, rolled)
        player["size"] += delta
        if player["size"] < 0:
            player["size"] = 0
        player["last"] = int(now.timestamp())
        player["name"] = user.first_name
        storage[uid_str] = player

        infection = roll_infection(eff.diseases_enabled)
        disease_msg = ""
        if infection:
            player["disease"] = {"id": infection.id, "caught_at": int(now.timestamp())}
            disease_msg = f"\n\n{infection.catch_message}"

        mention = _mention(user_id, user.first_name)
        rank = _rank(storage, user_id)
        remaining = _time_until_midnight(now)

        if delta >= 0:
            change_text = texts.dick_grew(delta)
        else:
            change_text = texts.dick_shrank(delta)

        dtag = disease_tag(player)
        text = texts.dick_result(mention, change_text, player["size"], rank, remaining)
        if dtag and not infection:
            text += f"\n{dtag}"
        text += disease_msg

        await save_storage(chat_id, storage)

        ts = int(now.timestamp())
        await E.ensure_baseline(chat_id, user_id, before, created_at=ts)
        await E.log_event(
            chat_id, user_id, E.DICK,
            delta=player["size"] - before,
            size_after=player["size"],
            meta={"rolled": rolled},
            created_at=ts,
        )
        if infection:
            await E.log_event(
                chat_id, user_id, E.INFECTION,
                size_after=player["size"],
                meta={"disease_id": infection.id},
                created_at=ts,
            )

        await message.answer(text, parse_mode="HTML")
