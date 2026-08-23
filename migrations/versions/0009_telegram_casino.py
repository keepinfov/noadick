"""Persist Telegram casino preferences and cooldowns.

Revision ID: 0009_telegram_casino
Revises: 0008_season_sekasko_prizes
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_telegram_casino"
down_revision = "0008_season_sekasko_prizes"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "players" in tables:
        existing = _columns(bind, "players")
        if "casino_stake" not in existing:
            op.add_column(
                "players",
                sa.Column("casino_stake", sa.Integer(), nullable=False, server_default="5"),
            )
        if "last_casino_spin_at" not in existing:
            op.add_column(
                "players",
                sa.Column("last_casino_spin_at", sa.Integer(), nullable=False, server_default="0"),
            )

    if "chat_settings" in tables and "casino_enabled" not in _columns(bind, "chat_settings"):
        op.add_column(
            "chat_settings",
            sa.Column("casino_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "chat_settings" in tables and "casino_enabled" in _columns(bind, "chat_settings"):
        op.drop_column("chat_settings", "casino_enabled")
    if "players" in tables:
        existing = _columns(bind, "players")
        if "last_casino_spin_at" in existing:
            op.drop_column("players", "last_casino_spin_at")
        if "casino_stake" in existing:
            op.drop_column("players", "casino_stake")
