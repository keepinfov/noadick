"""Control how much of the deposit claims a Corporation must cover to leave recovery.

Revision ID: 0012_corp_recovery_rule
Revises: 0011_casino_payout_rules
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_corp_recovery_rule"
down_revision = "0011_casino_payout_rules"
branch_labels = None
depends_on = None

COLUMN = "corp_recovery_liability_pct"
DEFAULT = "100"


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "global_settings" not in sa.inspect(bind).get_table_names():
        return
    if COLUMN not in _columns(bind, "global_settings"):
        op.add_column(
            "global_settings",
            sa.Column(COLUMN, sa.Integer(), nullable=False, server_default=DEFAULT),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "global_settings" not in sa.inspect(bind).get_table_names():
        return
    if COLUMN in _columns(bind, "global_settings"):
        with op.batch_alter_table("global_settings") as batch_op:
            batch_op.drop_column(COLUMN)
