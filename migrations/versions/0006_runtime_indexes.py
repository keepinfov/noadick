"""Runtime and collector indexes.

Revision ID: 0006_runtime_indexes
Revises: 0005_analytics
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_runtime_indexes"
down_revision = "0005_analytics"
branch_labels = None
depends_on = None


def _indexes(bind: sa.Connection, table: str) -> set[str]:
    return {
        name for index in sa.inspect(bind).get_indexes(table) if (name := index["name"]) is not None
    }


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    definitions = {
        "chat_corporations": (
            "ix_chat_corporations_status",
            ["status"],
        ),
        "deposit_insurance": (
            "ix_deposit_insurance_owner_expiry",
            ["chat_id", "user_id", "expires_at"],
        ),
        "loans": ("ix_loans_default_due", ["defaulted", "due_at"]),
    }
    for table, (name, columns) in definitions.items():
        if table in tables and name not in _indexes(bind, table):
            op.create_index(name, table, columns)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table, name in (
        ("loans", "ix_loans_default_due"),
        ("deposit_insurance", "ix_deposit_insurance_owner_expiry"),
        ("chat_corporations", "ix_chat_corporations_status"),
    ):
        if table in tables and name in _indexes(bind, table):
            op.drop_index(name, table_name=table)
