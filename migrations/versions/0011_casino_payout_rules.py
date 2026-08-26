"""Store globally configurable Telegram casino payout rules.

Revision ID: 0011_casino_payout_rules
Revises: 0010_public_profile_label
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_casino_payout_rules"
down_revision = "0010_public_profile_label"
branch_labels = None
depends_on = None


def _default_rules() -> list[dict[str, int | str]]:
    rules: list[dict[str, int | str]] = []
    for slot_value in range(1, 65):
        encoded = slot_value - 1
        symbols = (
            encoded & 0b11,
            (encoded >> 2) & 0b11,
            (encoded >> 4) & 0b11,
        )
        if symbols == (3, 3, 3):
            payout_value = 18
        elif symbols.count(3) == 2:
            payout_value = 3
        elif len(set(symbols)) == 1:
            payout_value = 5
        else:
            continue
        rules.append(
            {
                "slot_value": slot_value,
                "payout_kind": "multiplier",
                "payout_value": payout_value,
                "created_at": 0,
                "updated_at": 0,
            }
        )
    return rules


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "casino_payout_rules" not in tables:
        op.create_table(
            "casino_payout_rules",
            sa.Column("slot_value", sa.Integer(), nullable=False),
            sa.Column("payout_kind", sa.String(length=16), nullable=False),
            sa.Column("payout_value", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "updated_at",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.CheckConstraint(
                "slot_value BETWEEN 1 AND 64",
                name="ck_casino_payout_rules_slot_value",
            ),
            sa.CheckConstraint(
                "(payout_kind = 'multiplier' AND payout_value BETWEEN 0 AND 100) "
                "OR (payout_kind = 'fixed' AND payout_value BETWEEN 0 AND 5000)",
                name="ck_casino_payout_rules_kind_value",
            ),
            sa.PrimaryKeyConstraint("slot_value"),
        )

    table = sa.table(
        "casino_payout_rules",
        sa.column("slot_value", sa.Integer()),
        sa.column("payout_kind", sa.String(length=16)),
        sa.column("payout_value", sa.Integer()),
        sa.column("created_at", sa.Integer()),
        sa.column("updated_at", sa.Integer()),
    )
    existing = set(bind.execute(sa.select(table.c.slot_value)).scalars())
    missing = [rule for rule in _default_rules() if rule["slot_value"] not in existing]
    if missing:
        op.bulk_insert(table, missing)


def downgrade() -> None:
    bind = op.get_bind()
    if "casino_payout_rules" in sa.inspect(bind).get_table_names():
        op.drop_table("casino_payout_rules")
