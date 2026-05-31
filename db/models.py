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
    created_at: Mapped[int] = mapped_column(Integer, default=_now)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)

    chat: Mapped[Chat] = relationship(back_populates="players")


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
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)


class ChatSettings(Base):
    """Per-chat overrides for gameplay knobs. Missing row / NULL field means
    "use the process-wide default" (env TZ, hardcoded duel params, etc.)."""

    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tz: Mapped[str | None] = mapped_column(String, nullable=True)
    diseases_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    duel_stake_default: Mapped[int] = mapped_column(Integer, default=5)
    duel_timeout: Mapped[int] = mapped_column(Integer, default=60)
    updated_at: Mapped[int] = mapped_column(Integer, default=_now, onupdate=_now)
