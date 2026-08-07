"""Add renewable PISYAGO protection for low-asset players.

Revision ID: 0003_pisyago_insurance
Revises: 0002_poker_tables
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_pisyago_insurance"
down_revision = "0002_poker_tables"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "players" in sa.inspect(bind).get_table_names():
        existing = _columns(bind, "players")
        if "insurance_used" not in existing:
            op.add_column(
                "players",
                sa.Column("insurance_used", sa.Integer(), nullable=False, server_default="0"),
            )
        if "insurance_reset_at" not in existing:
            op.add_column(
                "players",
                sa.Column("insurance_reset_at", sa.Integer(), nullable=False, server_default="0"),
            )

    if "global_settings" in sa.inspect(bind).get_table_names():
        existing = _columns(bind, "global_settings")
        definitions = (
            ("dick_insurance_threshold", "20"),
            ("dick_insurance_limit", "20"),
            ("dick_insurance_period_days", "7"),
        )
        for name, default in definitions:
            if name not in existing:
                op.add_column(
                    "global_settings",
                    sa.Column(name, sa.Integer(), nullable=False, server_default=default),
                )


def downgrade() -> None:
    op.drop_column("global_settings", "dick_insurance_period_days")
    op.drop_column("global_settings", "dick_insurance_limit")
    op.drop_column("global_settings", "dick_insurance_threshold")
    op.drop_column("players", "insurance_reset_at")
    op.drop_column("players", "insurance_used")
