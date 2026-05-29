from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import texts
from models.disease import check_expire, disease_tag
from repositories.players import get_chat_lock, get_storage, save_storage

router = Router()


@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    chat_id = message.chat.id

    async with get_chat_lock(chat_id):
        storage = await get_storage(chat_id)

        changed = False
        for pid in list(storage.keys()):
            if check_expire(storage[pid]):
                changed = True
        if changed:
            await save_storage(chat_id, storage)

    if not storage:
        await message.answer(texts.TOP_EMPTY)
        return

    players = sorted(storage.items(), key=lambda x: x[1]["size"], reverse=True)
    top10 = players[:10]

    lines = [texts.TOP_HEADER]
    for i, (_uid, player) in enumerate(top10):
        tag = disease_tag(player)
        lines.append(texts.top_line(i + 1, player["name"], tag, player["size"]))

    await message.answer("\n".join(lines), parse_mode="HTML")
