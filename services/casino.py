"""Atomic economy service for Telegram's server-generated slot dice."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import get_session_factory
from db.models import ChatCorporation, ChatSettings, Player
from repositories import bank as bank_repo
from repositories import events as E
from repositories import players as players_repo
from services import settings
from services.bank import (
    BailInInfo,
    apply_bail_in_in,
    record_bail_in_in,
    required_reserve,
)
from services.global_settings import get_config_sync

MIN_STAKE = 1
MAX_STAKE = 50
DEFAULT_STAKE = 5
COOLDOWN_SECONDS = 4

BAR = "bar"
GRAPES = "grapes"
LEMON = "lemon"
SEVEN = "seven"
SLOT_SYMBOLS = (BAR, GRAPES, LEMON, SEVEN)


class CasinoError(Exception):
    """Expected rejection suitable for translation by the Telegram handler."""

    def __init__(self, code: str, *, retry_after: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class SlotOutcome:
    value: int
    message_id: int = 0


@dataclass(frozen=True)
class CasinoResult:
    stake: int
    value: int
    message_id: int
    symbols: tuple[str, str, str]
    multiplier: int
    gross_payout: int
    net: int
    size_after: int
    corporation_balance: int
    deficit: int
    bail_in: BailInInfo | None = None


OutcomeSupplier = Callable[[], Awaitable[int | SlotOutcome]]


def _validate_stake(stake: int) -> int:
    if isinstance(stake, bool) or not isinstance(stake, int):
        raise CasinoError("bad_stake")
    if not MIN_STAKE <= stake <= MAX_STAKE:
        raise CasinoError("bad_stake")
    return stake


def decode_slot(value: int) -> tuple[str, str, str]:
    """Decode Telegram's 1..64 slot value into its three two-bit reels."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
        raise CasinoError("bad_outcome")
    encoded = value - 1
    return (
        SLOT_SYMBOLS[encoded & 0b11],
        SLOT_SYMBOLS[(encoded >> 2) & 0b11],
        SLOT_SYMBOLS[(encoded >> 4) & 0b11],
    )


def payout_multiplier(value: int) -> int:
    symbols = decode_slot(value)
    if symbols == (SEVEN, SEVEN, SEVEN):
        return 18
    if symbols.count(SEVEN) == 2:
        return 3
    if len(set(symbols)) == 1:
        return 5
    return 0


async def get_stake(chat_id: int, user_id: int) -> int:
    player = await players_repo.get_player(chat_id, user_id)
    return _validate_stake(int(player.casino_stake)) if player else DEFAULT_STAKE


async def save_stake(chat_id: int, user_id: int, stake: int) -> int:
    """Persist a default only; this never spins or changes liquid size."""
    stake = _validate_stake(stake)
    async with players_repo.get_chat_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session, session.begin():
            player = await players_repo.get_player_in(session, chat_id, user_id)
            if player is not None and player.is_chat_banned:
                raise CasinoError("locally_banned")
            if player is None:
                player = await players_repo.ensure_player_in(session, chat_id, user_id)
            player.casino_stake = stake
    return stake


def _effective_stake(player: Player, override: int | None) -> int:
    return _validate_stake(int(player.casino_stake) if override is None else override)


def _check_player(player: Player | None, stake: int | None, now: int) -> int:
    if player is None:
        raise CasinoError("no_player")
    if player.is_chat_banned:
        raise CasinoError("locally_banned")
    actual_stake = _effective_stake(player, stake)
    if player.size <= 0:
        raise CasinoError("no_size")
    if player.size < actual_stake:
        raise CasinoError("insufficient")
    last_spin = int(player.last_casino_spin_at)
    elapsed = now - last_spin
    if last_spin > 0 and elapsed < COOLDOWN_SECONDS:
        raise CasinoError("cooldown", retry_after=max(1, last_spin + COOLDOWN_SECONDS - now))
    return actual_stake


async def _preflight(chat_id: int, user_id: int, stake: int | None, now: int) -> int:
    effective = await settings.get_effective(chat_id)
    if not effective.casino_enabled:
        raise CasinoError("disabled")
    factory = get_session_factory()
    async with factory() as session:
        player = await session.get(Player, (chat_id, user_id))
        actual_stake = _check_player(player, stake, now)
        corp = await session.get(ChatCorporation, chat_id)
        if corp is not None and corp.status != "healthy":
            raise CasinoError("corp_frozen")
        return actual_stake


def _normalize_outcome(outcome: int | SlotOutcome) -> SlotOutcome:
    normalized = SlotOutcome(outcome) if isinstance(outcome, int) else outcome
    if not isinstance(normalized, SlotOutcome):
        raise CasinoError("bad_outcome")
    decode_slot(normalized.value)
    if isinstance(normalized.message_id, bool) or not isinstance(normalized.message_id, int):
        raise CasinoError("bad_outcome")
    return normalized


async def _settle(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    *,
    stake_override: int | None,
    outcome: SlotOutcome,
    now: int,
) -> CasinoResult:
    chat_settings = await session.get(ChatSettings, chat_id)
    if chat_settings is not None and not chat_settings.casino_enabled:
        raise CasinoError("disabled")

    player = await session.get(Player, (chat_id, user_id))
    stake = _check_player(player, stake_override, now)
    assert player is not None
    corp = await bank_repo.ensure_corp_in(session, chat_id)
    if corp.status != "healthy":
        raise CasinoError("corp_frozen")

    multiplier = payout_multiplier(outcome.value)
    symbols = decode_slot(outcome.value)
    gross_payout = stake * multiplier
    net = gross_payout - stake
    liability = await bank_repo.deposit_liability_in(session, chat_id)
    projected_balance = int(corp.balance) - net
    cfg = get_config_sync()
    bail_in = None
    if net > 0 and projected_balance < required_reserve(liability, cfg):
        bail_in = await apply_bail_in_in(
            session,
            chat_id,
            now,
            cfg,
            initiator_user_id=user_id,
        )

    player.size += net
    player.last_casino_spin_at = now
    await session.flush()
    remaining_liability = await bank_repo.deposit_liability_in(session, chat_id)
    final_balance = int(corp.balance) - net
    deficit = max(
        0,
        -final_balance,
        remaining_liability - (final_balance + int(corp.insurance_reserve)),
    )
    if bail_in is not None:
        corp.status = "recovery" if deficit > 0 else "healthy"
        corp.sanation_started_at = 0
        corp.sanation_deadline = 0
        bail_in.payout = gross_payout
        bail_in.balance = final_balance
        bail_in.deficit = deficit
        bail_in.status = corp.status

    bail_meta = None
    if bail_in is not None:
        bail_meta = {
            "wiped": bail_in.wiped,
            "protected": bail_in.protected_claims,
            "payout": gross_payout,
            "balance": final_balance,
            "deficit": deficit,
            "status": corp.status,
        }
    meta = {
        "value": outcome.value,
        "message_id": outcome.message_id,
        "symbols": list(symbols),
        "stake": stake,
        "multiplier": multiplier,
        "gross_payout": gross_payout,
        "net": net,
        "balance": final_balance,
        "deficit": deficit,
        "bail_in": bail_meta,
    }
    actual_balance = await bank_repo.corp_apply_in(
        session,
        chat_id,
        delta=-net,
        reason="casino_spin",
        user_id=user_id,
        meta=meta,
    )
    if actual_balance != final_balance:
        raise RuntimeError("casino corporation balance drift")
    if bail_in is not None:
        await record_bail_in_in(
            session,
            chat_id,
            bail_in,
            initiator_user_id=user_id,
            force=True,
            source="casino",
        )
    E.add_event(
        session,
        chat_id,
        user_id,
        E.CASINO_SPIN,
        delta=net,
        size_after=player.size,
        meta=meta,
        created_at=now,
    )
    return CasinoResult(
        stake=stake,
        value=outcome.value,
        message_id=outcome.message_id,
        symbols=symbols,
        multiplier=multiplier,
        gross_payout=gross_payout,
        net=net,
        size_after=player.size,
        corporation_balance=final_balance,
        deficit=deficit,
        bail_in=bail_in,
    )


async def play(
    chat_id: int,
    user_id: int,
    outcome_supplier: OutcomeSupplier,
    *,
    stake: int | None = None,
    now: int | None = None,
) -> CasinoResult:
    """Run one spin while serializing all player and Corporation state.

    ``stake`` is a one-off override and never changes ``Player.casino_stake``.
    The read-only preflight transaction is closed before the supplier is
    awaited, so Telegram latency never keeps a database transaction open.
    """
    timestamp = int(time.time()) if now is None else int(now)
    if stake is not None:
        _validate_stake(stake)
    async with players_repo.get_chat_lock(chat_id), bank_repo.corp_lock(chat_id):
        await _preflight(chat_id, user_id, stake, timestamp)
        supplied = outcome_supplier()
        if not inspect.isawaitable(supplied):
            raise TypeError("outcome_supplier must return an awaitable")
        outcome = _normalize_outcome(await supplied)

        factory = get_session_factory()
        async with factory() as session, session.begin():
            return await _settle(
                session,
                chat_id,
                user_id,
                stake_override=stake,
                outcome=outcome,
                now=timestamp,
            )
