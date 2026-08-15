"""Persist immutable chat-local weekly season snapshots.

Revision ID: 0007_weekly_seasons
Revises: 0006_runtime_indexes
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_weekly_seasons"
down_revision = "0006_runtime_indexes"
branch_labels = None
depends_on = None


def _indexes(bind: sa.Connection, table: str) -> set[str]:
    return {
        name for index in sa.inspect(bind).get_indexes(table) if (name := index["name"]) is not None
    }


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "weekly_seasons" not in tables:
        op.create_table(
            "weekly_seasons",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("season_number", sa.Integer(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("starts_at", sa.Integer(), nullable=False),
            sa.Column("ends_at", sa.Integer(), nullable=False),
            sa.Column("tracking_since", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="live"),
            sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("is_empty", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "publication_status", sa.String(length=16), nullable=False, server_default="pending"
            ),
            sa.Column("published_at", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("published_message_id", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("length_start", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("length_end", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wealth_start", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wealth_end", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("emission", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("best_dick_delta", sa.Integer(), nullable=True),
            sa.Column("best_dick_user_id", sa.BigInteger(), nullable=True),
            sa.Column("worst_dick_delta", sa.Integer(), nullable=True),
            sa.Column("worst_dick_user_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.Column("finalized_at", sa.Integer(), nullable=False, server_default="0"),
            sa.CheckConstraint("season_number >= 0", name="ck_weekly_season_number"),
            sa.CheckConstraint("ends_at > starts_at", name="ck_weekly_season_bounds"),
            sa.CheckConstraint(
                "tracking_since >= starts_at AND tracking_since < ends_at",
                name="ck_weekly_season_tracking",
            ),
            sa.CheckConstraint("status IN ('live', 'finalized')", name="ck_weekly_season_status"),
            sa.CheckConstraint(
                "publication_status IN ('pending', 'published', 'skipped')",
                name="ck_weekly_season_publication",
            ),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("chat_id", "season_number", name="uq_weekly_season_chat_number"),
        )
    season_indexes = _indexes(bind, "weekly_seasons")
    for name, columns, unique, where in (
        ("ix_weekly_seasons_chat_status", ["chat_id", "status"], False, None),
        ("ix_weekly_seasons_chat_end", ["chat_id", "ends_at"], False, None),
        ("uq_weekly_seasons_live_chat", ["chat_id"], True, sa.text("status = 'live'")),
    ):
        if name not in season_indexes:
            op.create_index(name, "weekly_seasons", columns, unique=unique, sqlite_where=where)

    tables = set(sa.inspect(bind).get_table_names())
    if "weekly_season_players" not in tables:
        op.create_table(
            "weekly_season_players",
            sa.Column("season_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("start_length", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("end_length", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("start_wealth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("end_wealth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("dick_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("dick_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active_days", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("best_dick_delta", sa.Integer(), nullable=True),
            sa.Column("worst_dick_delta", sa.Integer(), nullable=True),
            sa.Column("wealth_delta", sa.Integer(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(["season_id"], ["weekly_seasons.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("season_id", "user_id"),
        )
    player_indexes = _indexes(bind, "weekly_season_players")
    for name, columns in (
        ("ix_weekly_season_players_dick", ["season_id", "dick_total"]),
        ("ix_weekly_season_players_wealth", ["season_id", "wealth_delta"]),
    ):
        if name not in player_indexes:
            op.create_index(name, "weekly_season_players", columns)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "weekly_season_players" in tables:
        op.drop_table("weekly_season_players")
    if "weekly_seasons" in tables:
        op.drop_table("weekly_seasons")
