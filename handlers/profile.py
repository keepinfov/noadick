import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from models.disease import DISEASE_BY_ID
from services import stats as S

router = Router()


def _mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


@router.message(Command("me"))
async def cmd_me(message: Message) -> None:
    chat_id = message.chat.id
    reply = message.reply_to_message
    target = reply.from_user if reply and reply.from_user else message.from_user
    if not target:
        return

    user_id = target.id
    profile = await S.compute_profile(chat_id, user_id)

    if not profile.exists:
        await message.answer(
            f"{_mention(user_id, target.first_name)} ещё не играл. "
            f"Измерь письку командой /dick!",
            parse_mode="HTML",
        )
        return

    timeline = await S.size_timeline(chat_id, user_id)
    size_spark = S.sparkline([size for _, size in timeline])
    deltas = await S.daily_deltas(chat_id, user_id)
    delta_spark = S.sparkline([d for _, d in deltas])

    name = _mention(user_id, profile.name)
    lines = [
        f"Профиль {name}",
        f"Размер: {profile.current_size} см ({profile.rank} место в топе)",
        "",
        f"Игр /dick: {profile.plays} за {profile.days_played} дн.",
        f"Всего вырос: +{profile.total_grown} см / потерял: −{profile.total_lost} см",
    ]
    if profile.best_day is not None and profile.worst_day is not None:
        lines.append(
            f"Лучший бросок: {profile.best_day:+d} см | "
            f"худший: {profile.worst_day:+d} см"
        )

    lines.append("")
    lines.append(
        f"Дуэли: {profile.duels_total} "
        f"(W{profile.wins}/L{profile.losses}, винрейт {profile.winrate:.0%})"
    )
    lines.append(
        f"Отжато: +{profile.stolen_total} см | "
        f"проиграно: −{profile.lost_in_duels} см"
    )
    lines.append(f"Заражений: {profile.diseases_caught}")

    if profile.current_disease:
        d = DISEASE_BY_ID.get(profile.current_disease)
        if d:
            lines.append(f"Сейчас болеет: {d.name}")

    if size_spark:
        lines.append("")
        lines.append(f"Размер во времени: {size_spark}")
    if delta_spark:
        lines.append(f"Дельты /dick ({len(deltas)} дн.): {delta_spark}")

    await message.answer("\n".join(lines), parse_mode="HTML")
