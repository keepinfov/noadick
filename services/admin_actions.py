"""Reusable admin mutation functions.

These are intentionally free of any aiogram/Telegram dependency so the same
functions can back both the in-Telegram admin UI and a future web panel.
Every mutation records an entry in the audit_log.
"""

from __future__ import annotations

from dataclasses import dataclass

import texts
from db.engine import get_session_factory
from db.models import AuditLog, Chat, ChatCorporation, User
from models.disease import DISEASE_BY_ID
from repositories import bank as bank_repo
from repositories import broadcasts as broadcasts_repo
from repositories import chats as chats_repo
from repositories import events as E
from repositories import players as players_repo
from repositories.players import get_chat_lock, now_ts
from services import economy_reform
from services.admins import is_global_admin
from services.bank import recovery_deficit
from services.global_settings import get_config_sync


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


async def set_size(actor_id: int, chat_id: int, user_id: int, size: int) -> ActionResult:
    size = max(0, int(size))
    async with get_chat_lock(chat_id):
        current = await players_repo.get_player(chat_id, user_id)
        before = current.size if current else 0
        p = await players_repo.set_player_fields(chat_id, user_id, size=size)
    await E.ensure_baseline(chat_id, user_id, before)
    await E.log_event(chat_id, user_id, E.ADMIN_ADJUST, delta=size - before, size_after=size)
    await _audit(
        actor_id,
        "set_size",
        target_chat=chat_id,
        target_user=user_id,
        payload={"size": size},
    )
    return ActionResult(True, texts.res_size_set(p.name, size))


async def add_size(actor_id: int, chat_id: int, user_id: int, delta: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        current = await players_repo.get_player(chat_id, user_id)
        base = current.size if current else 0
        new_size = max(0, base + int(delta))
        p = await players_repo.set_player_fields(chat_id, user_id, size=new_size)
    await E.ensure_baseline(chat_id, user_id, base)
    await E.log_event(
        chat_id,
        user_id,
        E.ADMIN_ADJUST,
        delta=new_size - base,
        size_after=new_size,
    )
    await _audit(
        actor_id,
        "add_size",
        target_chat=chat_id,
        target_user=user_id,
        payload={"delta": delta, "size": new_size},
    )
    return ActionResult(True, texts.res_size_add(p.name, new_size, delta))


async def set_name(actor_id: int, chat_id: int, user_id: int, name: str) -> ActionResult:
    async with get_chat_lock(chat_id):
        await players_repo.set_player_fields(chat_id, user_id, name=name)
    await _audit(
        actor_id,
        "set_name",
        target_chat=chat_id,
        target_user=user_id,
        payload={"name": name},
    )
    return ActionResult(True, texts.res_name_set(name))


def _normalize_public_label(label: str | None) -> tuple[bool, str | None]:
    if label is None:
        return True, None
    if not isinstance(label, str) or "\r" in label or "\n" in label:
        return False, None
    normalized = label.strip()
    if normalized == "-":
        return True, None
    if not normalized or len(normalized) > texts.MAX_PUBLIC_LABEL_LEN:
        return False, None
    return True, normalized


async def set_public_label(actor_id: int, user_id: int, label: str | None) -> ActionResult:
    """Set a global public profile label and its audit row atomically."""
    valid, normalized = _normalize_public_label(label)
    if not valid:
        return ActionResult(False, texts.RES_PUBLIC_LABEL_INVALID)

    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return ActionResult(False, texts.RES_USER_NOT_FOUND)
        before = user.public_label
        user.public_label = normalized
        session.add(
            AuditLog(
                actor_id=actor_id,
                action="set_public_label",
                target_user=user_id,
                payload={"before": before, "after": normalized},
            )
        )
        await session.commit()
    return ActionResult(True, texts.res_public_label_set(normalized))


async def give_disease(actor_id: int, chat_id: int, user_id: int, disease_id: str) -> ActionResult:
    if disease_id not in DISEASE_BY_ID:
        return ActionResult(False, texts.res_unknown_disease(disease_id))
    async with get_chat_lock(chat_id):
        await players_repo.set_player_fields(
            chat_id, user_id, disease_id=disease_id, disease_caught_at=now_ts()
        )
    await _audit(
        actor_id,
        "give_disease",
        target_chat=chat_id,
        target_user=user_id,
        payload={"disease_id": disease_id},
    )
    return ActionResult(True, texts.res_disease_given(DISEASE_BY_ID[disease_id].name))


async def cure(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        await players_repo.set_player_fields(
            chat_id, user_id, disease_id=None, disease_caught_at=None
        )
    await _audit(
        actor_id,
        "cure",
        target_chat=chat_id,
        target_user=user_id,
    )
    return ActionResult(True, texts.RES_CURED)


async def reset_player(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        current = await players_repo.get_player(chat_id, user_id)
        before = current.size if current else 0
        await players_repo.set_player_fields(
            chat_id,
            user_id,
            size=0,
            last_play=0,
            disease_id=None,
            disease_caught_at=None,
        )
    await E.ensure_baseline(chat_id, user_id, before)
    await E.log_event(chat_id, user_id, E.ADMIN_ADJUST, delta=-before, size_after=0)
    await _audit(
        actor_id,
        "reset_player",
        target_chat=chat_id,
        target_user=user_id,
    )
    return ActionResult(True, texts.RES_PLAYER_RESET)


async def delete_player(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        deleted = await players_repo.delete_player(chat_id, user_id)
    await _audit(
        actor_id,
        "delete_player",
        target_chat=chat_id,
        target_user=user_id,
    )
    if not deleted:
        return ActionResult(False, texts.RES_PLAYER_NOT_FOUND)
    return ActionResult(True, texts.RES_PLAYER_DELETED)


async def reset_chat(actor_id: int, chat_id: int) -> ActionResult:
    async with get_chat_lock(chat_id):
        n = await players_repo.reset_chat_players(chat_id)
    await _audit(actor_id, "reset_chat", target_chat=chat_id, payload={"removed": n})
    return ActionResult(True, texts.res_chat_reset(n))


async def preview_health_reform(chat_id: int) -> economy_reform.ReformResult:
    return await economy_reform.preview(chat_id)


async def apply_health_reform(actor_id: int, chat_id: int) -> economy_reform.ReformResult:
    return await economy_reform.apply(chat_id, actor_id)


async def local_unban(actor_id: int, chat_id: int, user_id: int) -> ActionResult:
    """Lift a per-chat (local) ban on a player; records an audit entry."""
    async with get_chat_lock(chat_id):
        p = await players_repo.get_player(chat_id, user_id)
        name = p.name if p else str(user_id)
        was_banned = bool(p and p.is_chat_banned)
        if was_banned:
            await players_repo.set_player_fields(chat_id, user_id, is_chat_banned=False)
    if not was_banned:
        return ActionResult(False, texts.mod_localunban_already(name))
    await _audit(
        actor_id,
        "local_unban",
        target_chat=chat_id,
        target_user=user_id,
    )
    return ActionResult(True, texts.mod_localunban_done(name))


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
        actor_id,
        "ban_user",
        target_user=user_id,
        payload={"reason": reason, "ban_until": ban_until},
    )
    suffix = texts.ban_reason_suffix(reason)
    return ActionResult(True, texts.res_user_banned(user_id, suffix))


async def unban_user(actor_id: int, user_id: int) -> ActionResult:
    await chats_repo.set_user_banned(user_id, False)
    await _audit(actor_id, "unban_user", target_user=user_id)
    return ActionResult(True, texts.res_user_unbanned(user_id))


async def ban_chat(actor_id: int, chat_id: int, reason: str | None = None) -> ActionResult:
    ok = await chats_repo.set_chat_banned(chat_id, True)
    await _audit(actor_id, "ban_chat", target_chat=chat_id, payload={"reason": reason})
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
    await broadcasts_repo.insert_broadcast(actor_id, preview, target_mode, sent, failed)
    await _audit(
        actor_id,
        "broadcast",
        payload={"mode": target_mode, "sent": sent, "failed": failed},
    )


# ---- chat Corporation control ----


# Guards the panel against fat-fingered deltas while still allowing a deep
# negative till, which recovery and bail-ins legitimately produce.
CORP_BALANCE_LIMIT = 100_000


@dataclass(frozen=True)
class CorpSnapshot:
    chat_id: int
    status: str
    balance: int
    reserve: int
    liability: int
    deficit: int
    bankruptcies: int

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


async def corp_snapshot(chat_id: int) -> CorpSnapshot | None:
    """Read-only Corporation view; ``None`` when the chat is unknown."""
    cfg = get_config_sync()
    factory = get_session_factory()
    async with factory() as session:
        if await session.get(Chat, chat_id) is None:
            return None
        corp = await session.get(ChatCorporation, chat_id)
        liability = await bank_repo.deposit_liability_in(session, chat_id)
        balance = int(corp.balance) if corp is not None else 0
        reserve = int(corp.insurance_reserve) if corp is not None else 0
        return CorpSnapshot(
            chat_id=chat_id,
            status=corp.status if corp is not None else "healthy",
            balance=balance,
            reserve=reserve,
            liability=liability,
            deficit=recovery_deficit(balance, reserve, liability, cfg),
            bankruptcies=int(corp.bankruptcy_count) if corp is not None else 0,
        )


async def adjust_corp_balance(actor_id: int, chat_id: int, delta: int) -> ActionResult:
    """Move a chat Corporation's operating cash and re-derive its status.

    The move is booked through ``corp_apply_in``, so it lands in the append-only
    ``corporation_ledger`` as ``admin_adjust`` as well as in ``audit_log``. The
    status follows the same deficit rule the collector uses, which is what makes
    a top-up able to lift a stuck recovery immediately.
    """
    delta = int(delta)
    cfg = get_config_sync()
    async with get_chat_lock(chat_id), bank_repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            corp = await bank_repo.ensure_corp_in(session, chat_id)
            current = int(corp.balance)
            target = max(-CORP_BALANCE_LIMIT, min(CORP_BALANCE_LIMIT, current + delta))
            applied = target - current
            if applied == 0:
                return ActionResult(False, texts.RES_CORP_NO_CHANGE)
            balance = await bank_repo.corp_apply_in(
                session,
                chat_id,
                delta=applied,
                reason="admin_adjust",
                user_id=actor_id,
                meta={"actor_id": actor_id},
            )
            liability = await bank_repo.deposit_liability_in(session, chat_id)
            deficit = recovery_deficit(balance, corp.insurance_reserve, liability, cfg)
            status = "recovery" if deficit > 0 else "healthy"
            corp.status = status
            corp.sanation_started_at = 0
            corp.sanation_deadline = 0
    await _audit(
        actor_id,
        "corp_adjust",
        target_chat=chat_id,
        payload={
            "applied": applied,
            "balance": balance,
            "deficit": deficit,
            "status": status,
        },
    )
    return ActionResult(True, texts.res_corp_adjusted(applied, balance, status))


async def recompute_corp_status(actor_id: int, chat_id: int) -> ActionResult | None:
    """Re-derive one Corporation's status without moving any money."""
    cfg = get_config_sync()
    async with get_chat_lock(chat_id), bank_repo.corp_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            if await session.get(Chat, chat_id) is None:
                return None
            corp = await bank_repo.ensure_corp_in(session, chat_id)
            liability = await bank_repo.deposit_liability_in(session, chat_id)
            deficit = recovery_deficit(corp.balance, corp.insurance_reserve, liability, cfg)
            previous = corp.status
            status = "recovery" if deficit > 0 else "healthy"
            corp.status = status
            corp.sanation_started_at = 0
            corp.sanation_deadline = 0
    await _audit(
        actor_id,
        "corp_recompute_status",
        target_chat=chat_id,
        payload={"previous": previous, "status": status, "deficit": deficit},
    )
    return ActionResult(True, texts.res_corp_recomputed(previous, status, deficit))
