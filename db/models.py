from __future__ import annotations

import time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String, default="")
    hash: Mapped[str] = mapped_column(String, index=True, default="")
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)

    players: Mapped[list[Player]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String, default="")
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ban_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class Player(Base):
    """Per-chat game state for a user."""

    __tablename__ = "players"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    last_play: Mapped[int] = mapped_column(Integer, default=0)
    disease_id: Mapped[str | None] = mapped_column(String, nullable=True)
    disease_caught_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_chat_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Credit history feeding the loan-limit multiplier (see services/bank.py).
    loans_repaid: Mapped[int] = mapped_column(Integer, default=0)
    loans_defaulted: Mapped[int] = mapped_column(Integer, default=0)
    last_credit_reward_at: Mapped[int] = mapped_column(Integer, default=0)
    # PISYAGO is a renewable safety net for poor players. ``insurance_used``
    # counts covered centimetres in the current fixed window; reset_at starts
    # with the first covered loss, so idle players do not burn their allowance.
    insurance_used: Mapped[int] = mapped_column(Integer, default=0)
    insurance_reset_at: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)

    chat: Mapped[Chat] = relationship(back_populates="players")


class Corporation(Base):
    """The single global house account. Collects duel tax, loan interest,
    deposit penalties and confiscations from every chat; pays out deposit
    interest. Balance may go negative — that is the "bankruptcy" event."""

    __tablename__ = "corporation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    total_tax: Mapped[int] = mapped_column(Integer, default=0)
    total_interest_earned: Mapped[int] = mapped_column(Integer, default=0)
    total_interest_paid: Mapped[int] = mapped_column(Integer, default=0)
    total_penalties: Mapped[int] = mapped_column(Integer, default=0)
    total_poker_rake: Mapped[int] = mapped_column(Integer, default=0)
    rules_url_rude: Mapped[str] = mapped_column(String, default="")
    rules_url_strict: Mapped[str] = mapped_column(String, default="")
    deposits_reconciled: Mapped[bool] = mapped_column(Boolean, default=False)
    bank_rebalanced_v2: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class ChatCorporation(Base):
    """Per-chat operating bank. The legacy ``corporation`` row is retained only
    for published rule URLs and an auditable migration source."""

    __tablename__ = "chat_corporations"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(Integer, default=0)
    insurance_reserve: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="healthy")
    sanation_started_at: Mapped[int] = mapped_column(Integer, default=0)
    sanation_deadline: Mapped[int] = mapped_column(Integer, default=0)
    bankruptcy_count: Mapped[int] = mapped_column(Integer, default=0)
    crisis_message_id: Mapped[int] = mapped_column(Integer, default=0)
    crisis_thread_id: Mapped[int] = mapped_column(Integer, default=0)
    last_crisis_notice_at: Mapped[int] = mapped_column(Integer, default=0)
    total_tax: Mapped[int] = mapped_column(Integer, default=0)
    total_interest_earned: Mapped[int] = mapped_column(Integer, default=0)
    total_interest_paid: Mapped[int] = mapped_column(Integer, default=0)
    total_penalties: Mapped[int] = mapped_column(Integer, default=0)
    total_poker_rake: Mapped[int] = mapped_column(Integer, default=0)
    total_emission: Mapped[int] = mapped_column(Integer, default=0)
    total_bailin: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class CorporationLedger(Base):
    """Append-only audit trail for every chat-corporation money movement."""

    __tablename__ = "corporation_ledger"
    __table_args__ = (
        Index("ix_corp_ledger_chat_ts", "chat_id", "created_at"),
        Index("ix_corp_ledger_reason_ts", "reason", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str] = mapped_column(String(32))
    cash_delta: Mapped[int] = mapped_column(Integer, default=0)
    reserve_delta: Mapped[int] = mapped_column(Integer, default=0)
    balance_after: Mapped[int] = mapped_column(Integer, default=0)
    reserve_after: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class AnalyticsState(Base):
    """Installation-local watermark for metrics that require the v2 journal."""

    __tablename__ = "analytics_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    detailed_since: Mapped[int] = mapped_column(Integer, default=_now)
    backfill_completed_at: Mapped[int] = mapped_column(Integer, default=0)


class Deposit(Base):
    """One active deposit per (chat, user). Opening moves size out of the
    player (freezing it: hidden from /top, unusable in duels, no /dick growth);
    withdrawing returns principal + accrued − early-withdrawal penalty."""

    __tablename__ = "deposits"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal: Mapped[int] = mapped_column(Integer, default=0)
    accrued: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[int] = mapped_column(Integer, default=_now)
    matures_at: Mapped[int] = mapped_column(Integer, default=0)
    # Number of distinct active days that have earned interest (drives the
    # decaying effective rate). Interest is credited only on days the owner
    # actually plays /dick — passive deposits do not grow.
    active_days_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accrual_day: Mapped[str] = mapped_column(String, default="")
    # Fractional interest carried between active days in millionths of one
    # size unit. This keeps tiny deposits proportional without minting a free
    # minimum +1 on every accrual.
    interest_remainder_ppm: Mapped[int] = mapped_column(Integer, default=0)
    # Calendar day (UTC, ISO) of the last confiscation roll, so the collector
    # makes at most one attempt per day regardless of its run frequency.
    last_confisc_day: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class DepositInsurance(Base):
    """One SЕКАСКО coverage tranche. Tranches expire independently so buying a
    centimetre later cannot renew older coverage for free."""

    __tablename__ = "deposit_insurance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    premium: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class Loan(Base):
    """One active loan per (chat, user). Principal is credited to liquid size
    immediately; interest accrues by calendar time. Past due_at the loan is in
    default and is recovered via /dick and duel garnishment."""

    __tablename__ = "loans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal: Mapped[int] = mapped_column(Integer, default=0)
    # Portion of principal created by negative /dick rolls rather than cash
    # borrowed from the Corporation. It follows the same due/default/recovery
    # machinery but is shown separately to the player and in economy reports.
    roll_debt_principal: Mapped[int] = mapped_column(Integer, default=0)
    accrued_interest: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[int] = mapped_column(Integer, default=_now)
    due_at: Mapped[int] = mapped_column(Integer, default=0)
    last_accrual_at: Mapped[int] = mapped_column(Integer, default=_now)
    last_reminded_at: Mapped[int] = mapped_column(Integer, default=0)
    defaulted: Mapped[bool] = mapped_column(Boolean, default=False)
    original_cash_principal: Mapped[int] = mapped_column(Integer, default=0)
    credit_limit_at_open: Mapped[int] = mapped_column(Integer, default=0)
    rating_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String)
    target_chat: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_user: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class EconomyReform(Base):
    """One applied, versioned economy event per chat."""

    __tablename__ = "economy_reforms"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reform_key: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    affected_players: Mapped[int] = mapped_column(Integer, default=0)
    total_before: Mapped[int] = mapped_column(Integer, default=0)
    total_after: Mapped[int] = mapped_column(Integer, default=0)
    total_cut: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class BroadcastLog(Base):
    """One row per completed broadcast: who sent it, target mode, delivery
    counts, and a truncated preview of the text — for the history screen."""

    __tablename__ = "broadcast_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    preview: Mapped[str] = mapped_column(Text, default="")
    target_mode: Mapped[str] = mapped_column(String, default="all")
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class Event(Base):
    """Append-only log of size-affecting game events, used for /me stats and
    (later) chart generation."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_chat_user_ts", "chat_id", "user_id", "created_at"),
        Index("ix_events_chat_type_ts", "chat_id", "type", "created_at"),
        Index("ix_events_user_type_ts", "user_id", "type", "created_at"),
        Index("ix_events_type_ts", "type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String)
    delta: Mapped[int] = mapped_column(Integer, default=0)
    size_after: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class LegacyChat(Base):
    """Staging table for migrated JSON files keyed by md5(chat_id).

    The real chat_id is unknown until a message arrives from that chat and its
    md5 hash matches; then the data is relinked into chats/players.
    """

    __tablename__ = "legacy_chats"

    hash: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    relinked_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class ChatThreadStat(Base):
    """Per-chat forum-topic tracking: usage count (auto fallback) +
    explicit admin-pinned broadcast target (is_default)."""

    __tablename__ = "chat_thread_stats"
    __table_args__ = (Index("ix_cts_chat_default", "chat_id", "is_default"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class GlobalSettings(Base):
    """Process-wide tunables, edited from the admin panel (global admins only).
    Single row (id=1); a missing row means "use the hardcoded defaults". Defaults
    here mirror the historical hardcoded constants, so a fresh deploy behaves
    identically until an admin changes something."""

    __tablename__ = "global_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cd_duel: Mapped[int] = mapped_column(Integer, default=10)
    cd_top: Mapped[int] = mapped_column(Integer, default=5)
    cd_me: Mapped[int] = mapped_column(Integer, default=5)
    cd_ping: Mapped[int] = mapped_column(Integer, default=5)
    cd_help: Mapped[int] = mapped_column(Integer, default=5)
    cd_chat_ban_notice: Mapped[int] = mapped_column(Integer, default=300)
    cd_ban_notice: Mapped[int] = mapped_column(Integer, default=300)
    cd_dm_gate: Mapped[int] = mapped_column(Integer, default=300)
    max_pending_duels: Mapped[int] = mapped_column(Integer, default=3)
    size_weight_pct: Mapped[int] = mapped_column(Integer, default=20)
    bcast_rate_delay_ms: Mapped[int] = mapped_column(Integer, default=50)
    active_days: Mapped[int] = mapped_column(Integer, default=30)
    page_size: Mapped[int] = mapped_column(Integer, default=8)
    # Banking knobs (deposits / loans / collector). See services/global_settings.
    dep_rate_pct: Mapped[int] = mapped_column(Integer, default=2)
    dep_rate_decay_pct: Mapped[int] = mapped_column(Integer, default=25)
    dep_rate_floor_pct: Mapped[int] = mapped_column(Integer, default=0)
    dep_yield_cap_pct: Mapped[int] = mapped_column(Integer, default=8)
    dep_term_days: Mapped[int] = mapped_column(Integer, default=7)
    dep_early_penalty_pct: Mapped[int] = mapped_column(Integer, default=30)
    dep_confisc_chance_pct: Mapped[int] = mapped_column(Integer, default=2)
    dep_confisc_max_pct: Mapped[int] = mapped_column(Integer, default=10)
    loan_rate_pct: Mapped[int] = mapped_column(Integer, default=2)
    loan_max_base_pct: Mapped[int] = mapped_column(Integer, default=50)
    loan_min: Mapped[int] = mapped_column(Integer, default=15)
    loan_term_days: Mapped[int] = mapped_column(Integer, default=7)
    dick_debt_term_days: Mapped[int] = mapped_column(Integer, default=3)
    dick_insurance_threshold: Mapped[int] = mapped_column(Integer, default=20)
    dick_insurance_limit: Mapped[int] = mapped_column(Integer, default=20)
    dick_insurance_period_days: Mapped[int] = mapped_column(Integer, default=7)
    loan_garnish_pct: Mapped[int] = mapped_column(Integer, default=50)
    loan_deny_cooldown_sec: Mapped[int] = mapped_column(Integer, default=1800)
    loan_duel_garnish_pct: Mapped[int] = mapped_column(Integer, default=50)
    collector_interval_sec: Mapped[int] = mapped_column(Integer, default=3600)
    reminder_cooldown_sec: Mapped[int] = mapped_column(Integer, default=21600)
    dick_emission_cap: Mapped[int] = mapped_column(Integer, default=3)
    corp_liquidity_reserve_pct: Mapped[int] = mapped_column(Integer, default=25)
    corp_sanation_days: Mapped[int] = mapped_column(Integer, default=7)
    sekasko_max_coverage: Mapped[int] = mapped_column(Integer, default=40)
    sekasko_premium_pct: Mapped[int] = mapped_column(Integer, default=5)
    credit_reward_min_age_days: Mapped[int] = mapped_column(Integer, default=3)
    credit_reward_cooldown_days: Mapped[int] = mapped_column(Integer, default=14)
    credit_reward_min_limit_pct: Mapped[int] = mapped_column(Integer, default=25)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class ChatSettings(Base):
    """Per-chat overrides for gameplay knobs. Missing row / NULL field means
    "use the process-wide default" (env TZ, hardcoded duel params, etc.)."""

    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tz: Mapped[str | None] = mapped_column(String, nullable=True)
    diseases_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    banking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poker_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    duel_stake_default: Mapped[int] = mapped_column(Integer, default=5)
    duel_timeout: Mapped[int] = mapped_column(Integer, default=60)
    stats_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stats_digest_weekday: Mapped[int] = mapped_column(Integer, default=0)
    stats_digest_hour: Mapped[int] = mapped_column(Integer, default=10)
    stats_digest_last_week: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class PokerTable(Base):
    """Persistent Telegram poker room.

    Money rooms use ``chat_id`` as their bankroll namespace. Practice rooms
    have a NULL chat and never touch Player or Event rows.
    """

    __tablename__ = "poker_tables"
    __table_args__ = (
        Index("ix_poker_tables_location", "chat_id", "thread_id", "status"),
        Index("ix_poker_tables_host", "host_id", "status"),
    )

    table_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="money")
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    host_id: Mapped[int] = mapped_column(BigInteger, index=True)
    host_name: Mapped[str] = mapped_column(String(64), default="")
    access_mode: Mapped[str] = mapped_column(String(16), default="approval")
    max_seats: Mapped[int] = mapped_column(Integer, default=5)
    buy_in: Mapped[int] = mapped_column(Integer, default=40)
    small_blind: Mapped[int] = mapped_column(Integer, default=1)
    big_blind: Mapped[int] = mapped_column(Integer, default=2)
    turn_timeout: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(16), default="lobby")
    board_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hand_no: Mapped[int] = mapped_column(Integer, default=0)
    dealer_seat: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settings_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    close_after_hand: Mapped[bool] = mapped_column(Boolean, default=False)
    last_event: Mapped[str] = mapped_column(String(256), default="")
    host_active_at: Mapped[int] = mapped_column(Integer, default=_now)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class PokerSeat(Base):
    __tablename__ = "poker_seats"
    __table_args__ = (
        Index(
            "uq_poker_active_user",
            "user_id",
            unique=True,
            sqlite_where=sql_text("status = 'active'"),
        ),
        Index("ix_poker_seats_table_status", "table_id", "status", "seat_no"),
    )

    table_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("poker_tables.table_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seat_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64), default="")
    stack: Mapped[int] = mapped_column(Integer, default=0)
    committed: Mapped[int] = mapped_column(Integer, default=0)
    total_buyin: Mapped[int] = mapped_column(Integer, default=0)
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    sitting_out: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_leave: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_kick: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_timeouts: Mapped[int] = mapped_column(Integer, default=0)
    dm_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turn_notice_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turn_notice_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    joined_at: Mapped[int] = mapped_column(Integer, default=_now)
    left_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class PokerJoinRequest(Base):
    __tablename__ = "poker_join_requests"
    __table_args__ = (Index("ix_poker_requests_table_status", "table_id", "status"),)

    table_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("poker_tables.table_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class PokerInvite(Base):
    __tablename__ = "poker_invites"
    __table_args__ = (Index("ix_poker_invites_table", "table_id"),)

    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    table_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("poker_tables.table_id", ondelete="CASCADE")
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remaining_uses: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=_now)


class PokerHand(Base):
    __tablename__ = "poker_hands"
    __table_args__ = (
        Index("ix_poker_hands_table_status", "table_id", "status"),
        Index("uq_poker_hand_no", "table_id", "hand_no", unique=True),
    )

    hand_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("poker_tables.table_id", ondelete="CASCADE")
    )
    hand_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    street: Mapped[str] = mapped_column(String(16), default="preflop")
    state: Mapped[dict] = mapped_column(JSON)
    deadline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[int] = mapped_column(Integer, default=_now)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
