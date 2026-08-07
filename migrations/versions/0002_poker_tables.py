"""Add persistent Hold'em rooms and the per-chat poker switch.

Revision ID: 0002_poker_tables
Revises: 0001_managed_schema
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from db.models import Base

revision = "0002_poker_tables"
down_revision = "0001_managed_schema"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "chat_settings" in existing and "poker_enabled" not in _columns(bind, "chat_settings"):
        op.add_column(
            "chat_settings",
            sa.Column("poker_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "corporation" in existing and "total_poker_rake" not in _columns(bind, "corporation"):
        op.add_column(
            "corporation",
            sa.Column("total_poker_rake", sa.Integer(), nullable=False, server_default="0"),
        )
    if "loans" in existing and "roll_debt_principal" not in _columns(bind, "loans"):
        op.add_column(
            "loans",
            sa.Column("roll_debt_principal", sa.Integer(), nullable=False, server_default="0"),
        )
    if "global_settings" in existing and "dick_debt_term_days" not in _columns(
        bind, "global_settings"
    ):
        op.add_column(
            "global_settings",
            sa.Column("dick_debt_term_days", sa.Integer(), nullable=False, server_default="3"),
        )

    for name in (
        "poker_tables",
        "poker_seats",
        "poker_join_requests",
        "poker_invites",
        "poker_hands",
    ):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    for name in (
        "poker_hands",
        "poker_invites",
        "poker_join_requests",
        "poker_seats",
        "poker_tables",
    ):
        op.drop_table(name)
    op.drop_column("corporation", "total_poker_rake")
    op.drop_column("loans", "roll_debt_principal")
    op.drop_column("global_settings", "dick_debt_term_days")
    op.drop_column("chat_settings", "poker_enabled")
