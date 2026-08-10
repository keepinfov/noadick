"""Detailed analytics, corporation ledger and weekly digests.

Revision ID: 0005_analytics
Revises: 0004_local_corporations
Create Date: 2026-08-10
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

revision = "0005_analytics"
down_revision = "0004_local_corporations"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "chat_settings" in tables:
        existing = _columns(bind, "chat_settings")
        definitions = (
            ("stats_digest_enabled", sa.Boolean(), sa.false()),
            ("stats_digest_weekday", sa.Integer(), "0"),
            ("stats_digest_hour", sa.Integer(), "10"),
            ("stats_digest_last_week", sa.String(), ""),
        )
        for name, kind, default in definitions:
            if name not in existing:
                op.add_column(
                    "chat_settings",
                    sa.Column(name, kind, nullable=False, server_default=default),
                )

    if "corporation_ledger" not in tables:
        op.create_table(
            "corporation_ledger",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("reason", sa.String(length=32), nullable=False),
            sa.Column("cash_delta", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserve_delta", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("balance_after", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserve_after", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_corporation_ledger_chat_id", "corporation_ledger", ["chat_id"])
        op.create_index("ix_corp_ledger_chat_ts", "corporation_ledger", ["chat_id", "created_at"])
        op.create_index("ix_corp_ledger_reason_ts", "corporation_ledger", ["reason", "created_at"])

    if "analytics_state" not in tables:
        op.create_table(
            "analytics_state",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("detailed_since", sa.Integer(), nullable=False),
            sa.Column("backfill_completed_at", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
        )
    now = int(time.time())
    bind.execute(
        sa.text(
            "INSERT OR IGNORE INTO analytics_state "
            "(id, detailed_since, backfill_completed_at) VALUES (1, :now, :now)"
        ),
        {"now": now},
    )

    if "events" in tables:
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("events")}
        for name, columns in (
            ("ix_events_chat_type_ts", ["chat_id", "type", "created_at"]),
            ("ix_events_user_type_ts", ["user_id", "type", "created_at"]),
            ("ix_events_type_ts", ["type", "created_at"]),
        ):
            if name not in indexes:
                op.create_index(name, "events", columns)


def downgrade() -> None:
    raise RuntimeError("Analytics migration is irreversible; restore its backup")
