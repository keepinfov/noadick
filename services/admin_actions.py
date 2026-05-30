"""Reusable admin mutation functions.

These are intentionally free of any aiogram/Telegram dependency so the same
functions can back both the in-Telegram admin UI and a future web panel.
Every mutation records an entry in the audit_log.
"""
from __future__ import annotations

from dataclasses import dataclass

import texts
from db.engine import get_session_factory
from db.models import AuditLog
from models.disease import DISEASE_BY_ID
from repositories import broadcasts as broadcasts_repo
from repositories import chats as chats_repo
from repositories import events as E
from repositories import players as players_repo
from repositories.players import get_chat_lock, now_ts
from services.admins import is_global_admin


@dataclass
class ActionResult:
    ok: bool
    message: str


async def _audit(
    actor_id: int,
    action: str,
    *,
    target_chat: int | None = None,
    target_user: int | None = None,
    payload: dict | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                target_chat=target_chat,
                target_user=target_user,
                payload=payload,
            )
        )
        await session.commit()


async def set_size(
    actor_id: int, chat_id: int, user_id: int, size: int
) -> ActionResult:
    size = max(0, int(size))
    async with get_chat_lock(chat_id):
        current = await players_repo.get_player(chat_id, user_id)
        before = current.size if current else 0
        p = await players_repo.set_player_fields(chat_id, user_id, size=size)
    await E.ensure_baseline(chat_id, user_id, before)
    await E.log_event(
        chat_id, user_id, E.ADMIN_ADJUST, delta=size - before, size_after=size
    )
    await _audit(
        actor_id, "set_size", target_chat=chat_id, target_user=user_id,
        payload={"size": size},
    )
    return ActionResult(True, texts.res_size_set(p.name, size))


async def add_size(
    actor_id: int, chat_id: int, user_id: int, delta: int
) -> ActionResult:
    async with get_chat_lock(chat_id):
        current = await players_repo.get_player(chat_id, user_id)
        base = current.size if current else 0
        new_size = max(0, base + int(delta))
        p = await players_repo.set_player_fields(chat_id, user_id, size=new_size)
    await E.ensure_baseline(chat_id, user_id, base)
    await E.log_event(
        chat_id, user_id, E.ADMIN_ADJUST,
        delta=new_size - base, size_after=new_size,
    )
    await _audit(
        actor_id, "add_size", target_chat=chat_id, target_user=user_id,
        payload={"delta": delta, "size": new_size},
    )
    return ActionResult(True, texts.res_size_add(p.name, new_size, delta))


async def set_name(
    actor_id: int, chat_id: int, user_id: int, name: str
) -> ActionResult:
    async with get_chat_lock(chat_id):
        await players_repo.set_player_fields(chat_id, user_id, name=name)
    await _audit(
        actor_id, "set_name", target_chat=chat_id, target_user=user_id,
        payload={"name": name},
    )
    return ActionResult(True, texts.res_name_set(name))


async def give_disease(
    actor_id: int, chat_id: int, user_id: int, disease_id: str
) -> ActionResult:
    if disease_id not in DISEASE_BY_ID:
        return ActionResult(False, texts.res_unknown_disease(disease_id))
    async with get_chat_lock(chat_id):
        await players_repo.set_player_fields(
            chat_id, user_id, disease_id=disease_id, disease_caught_at=now_ts()
        )
    await _audit(
        actor_id, "give_disease", target_chat=chat_id, target_user=user_id,
        payload={"disease_id": disease_id},
    )
    return ActionResult(True, texts.res_disease_given(DISEASE_BY_ID[disease_id].name))


async def cure(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        await players_repo.set_player_fields(
            chat_id, user_id, disease_id=None, disease_caught_at=None
        )
    await _audit(
        actor_id, "cure", target_chat=chat_id, target_user=user_id,
    )
    return ActionResult(True, texts.RES_CURED)


async def reset_player(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        current = await players_repo.get_player(chat_id, user_id)
        before = current.size if current else 0
        await players_repo.set_player_fields(
            chat_id, user_id, size=0, last_play=0,
            disease_id=None, disease_caught_at=None,
        )
    await E.ensure_baseline(chat_id, user_id, before)
    await E.log_event(
        chat_id, user_id, E.ADMIN_ADJUST, delta=-before, size_after=0
    )
    await _audit(
        actor_id, "reset_player", target_chat=chat_id, target_user=user_id,
    )
    return ActionResult(True, texts.RES_PLAYER_RESET)


async def delete_player(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        deleted = await players_repo.delete_player(chat_id, user_id)
    await _audit(
        actor_id, "delete_player", target_chat=chat_id, target_user=user_id,
    )
    if not deleted:
        return ActionResult(False, texts.RES_PLAYER_NOT_FOUND)
    return ActionResult(True, texts.RES_PLAYER_DELETED)


async def reset_chat(actor_id: int, chat_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        n = await players_repo.reset_chat_players(chat_id)
    await _audit(actor_id, "reset_chat", target_chat=chat_id, payload={"removed": n})
    return ActionResult(True, texts.res_chat_reset(n))


async def ban_user(
    actor_id: int,
    user_id: int,
    reason: str | None = None,
    ban_until: int | None = None,
) -> ActionResult:
    if is_global_admin(user_id):
        return ActionResult(False, texts.RES_CANT_BAN_ADMIN)
    await chats_repo.set_user_banned(user_id, True, reason=reason, ban_until=ban_until)
    await _audit(
        actor_id, "ban_user", target_user=user_id,
        payload={"reason": reason, "ban_until": ban_until},
    )
    suffix = texts.ban_reason_suffix(reason)
    return ActionResult(True, texts.res_user_banned(user_id, suffix))


async def unban_user(actor_id: int, user_id: int) -> ActionResult:
    await chats_repo.set_user_banned(user_id, False)
    await _audit(actor_id, "unban_user", target_user=user_id)
    return ActionResult(True, texts.res_user_unbanned(user_id))


async def ban_chat(
    actor_id: int, chat_id: int, reason: str | None = None
) -> ActionResult:
    ok = await chats_repo.set_chat_banned(chat_id, True)
    await _audit(
        actor_id, "ban_chat", target_chat=chat_id, payload={"reason": reason}
    )
    return ActionResult(ok, texts.res_chat_banned(chat_id) if ok else texts.RES_CHAT_NOT_FOUND)


async def unban_chat(actor_id: int, chat_id: int) -> ActionResult:
    ok = await chats_repo.set_chat_banned(chat_id, False)
    await _audit(actor_id, "unban_chat", target_chat=chat_id)
    return ActionResult(ok, texts.res_chat_unbanned(chat_id) if ok else texts.RES_CHAT_NOT_FOUND)


async def broadcast_targets(mode: str = "all", active_days: int | None = None) -> list[int]:
    """Chat ids for a broadcast, filtered by target mode (excludes banned chats).
    Sending is done by the caller, which has access to the bot instance."""
    if active_days is None:
        return await chats_repo.chat_ids_by_mode(mode)
    return await chats_repo.chat_ids_by_mode(mode, active_days=active_days)


async def log_broadcast(
    actor_id: int, preview: str, target_mode: str, sent: int, failed: int
) -> None:
    """Persist a completed broadcast for the history screen (+ audit)."""
    await broadcasts_repo.insert_broadcast(
        actor_id, preview, target_mode, sent, failed
    )
    await _audit(
        actor_id, "broadcast",
        payload={"mode": target_mode, "sent": sent, "failed": failed},
    )
