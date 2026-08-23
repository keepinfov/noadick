"""Add a public developer label to global user profiles.

Revision ID: 0010_public_profile_label
Revises: 0009_telegram_casino
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_public_profile_label"
down_revision = "0009_telegram_casino"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "users" in sa.inspect(bind).get_table_names() and "public_label" not in _columns(
        bind, "users"
    ):
        op.add_column("users", sa.Column("public_label", sa.String(length=80), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "users" in sa.inspect(bind).get_table_names() and "public_label" in _columns(bind, "users"):
        op.drop_column("users", "public_label")
