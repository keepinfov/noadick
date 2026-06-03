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
    rules_url_rude: Mapped[str] = mapped_column(String, default="")
    rules_url_strict: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


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
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class Loan(Base):
    """One active loan per (chat, user). Principal is credited to liquid size
    immediately; interest accrues by calendar time. Past due_at the loan is in
    default and is recovered via /dick and duel garnishment."""

    __tablename__ = "loans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal: Mapped[int] = mapped_column(Integer, default=0)
    accrued_interest: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[int] = mapped_column(Integer, default=_now)
    due_at: Mapped[int] = mapped_column(Integer, default=0)
    last_accrual_at: Mapped[int] = mapped_column(Integer, default=_now)
    last_reminded_at: Mapped[int] = mapped_column(Integer, default=0)
    defaulted: Mapped[bool] = mapped_column(Boolean, default=False)
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
    __table_args__ = (Index("ix_events_chat_user_ts", "chat_id", "user_id", "created_at"),)

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
    dep_rate_pct: Mapped[int] = mapped_column(Integer, default=3)
    dep_rate_decay_pct: Mapped[int] = mapped_column(Integer, default=15)
    dep_rate_floor_pct: Mapped[int] = mapped_column(Integer, default=1)
    dep_yield_cap_pct: Mapped[int] = mapped_column(Integer, default=50)
    dep_term_days: Mapped[int] = mapped_column(Integer, default=7)
    dep_early_penalty_pct: Mapped[int] = mapped_column(Integer, default=30)
    dep_confisc_chance_pct: Mapped[int] = mapped_column(Integer, default=2)
    dep_confisc_max_pct: Mapped[int] = mapped_column(Integer, default=10)
    loan_rate_pct: Mapped[int] = mapped_column(Integer, default=5)
    loan_max_base_pct: Mapped[int] = mapped_column(Integer, default=100)
    loan_term_days: Mapped[int] = mapped_column(Integer, default=5)
    loan_garnish_pct: Mapped[int] = mapped_column(Integer, default=50)
    loan_duel_garnish_pct: Mapped[int] = mapped_column(Integer, default=50)
    collector_interval_sec: Mapped[int] = mapped_column(Integer, default=3600)
    reminder_cooldown_sec: Mapped[int] = mapped_column(Integer, default=21600)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class ChatSettings(Base):
    """Per-chat overrides for gameplay knobs. Missing row / NULL field means
    "use the process-wide default" (env TZ, hardcoded duel params, etc.)."""

    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tz: Mapped[str | None] = mapped_column(String, nullable=True)
    diseases_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    banking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    duel_stake_default: Mapped[int] = mapped_column(Integer, default=5)
    duel_timeout: Mapped[int] = mapped_column(Integer, default=60)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)
