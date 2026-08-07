"""Adopt the current schema and upgrade legacy databases.

Revision ID: 0001_managed_schema
Revises:
Create Date: 2026-08-04
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

from db.models import Base

revision = "0001_managed_schema"
down_revision = None
branch_labels = None
depends_on = None


_LEGACY_COLUMNS: dict[str, list[sa.Column]] = {
    "users": [
        sa.Column("banned_at", sa.Integer(), nullable=True),
        sa.Column("ban_until", sa.Integer(), nullable=True),
    ],
    "players": [
        sa.Column("is_chat_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("loans_repaid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loans_defaulted", sa.Integer(), nullable=False, server_default="0"),
    ],
    "loans": [
        sa.Column("roll_debt_principal", sa.Integer(), nullable=False, server_default="0"),
    ],
    "chat_settings": [
        sa.Column("banking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("poker_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    ],
    "corporation": [
        sa.Column("deposits_reconciled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bank_rebalanced_v2", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("total_poker_rake", sa.Integer(), nullable=False, server_default="0"),
    ],
    "deposits": [
        sa.Column("last_confisc_day", sa.String(), nullable=False, server_default=""),
        sa.Column("interest_remainder_ppm", sa.Integer(), nullable=False, server_default="0"),
    ],
    "global_settings": [
        sa.Column("dep_rate_pct", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("dep_rate_decay_pct", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("dep_rate_floor_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dep_yield_cap_pct", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("dep_term_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("dep_early_penalty_pct", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("dep_confisc_chance_pct", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("dep_confisc_max_pct", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("loan_rate_pct", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("loan_max_base_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("loan_min", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("loan_term_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("dick_debt_term_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("loan_garnish_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("loan_deny_cooldown_sec", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("loan_duel_garnish_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("collector_interval_sec", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("reminder_cooldown_sec", sa.Integer(), nullable=False, server_default="21600"),
    ],
}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    for table, definitions in _LEGACY_COLUMNS.items():
        existing = _columns(bind, table)
        for column in definitions:
            if column.name not in existing:
                op.add_column(table, column)

    if "cd_dick_repeat" in _columns(bind, "global_settings"):
        op.drop_column("global_settings", "cd_dick_repeat")

    for table in Base.metadata.tables.values():
        for index in table.indexes:
            index.create(bind=bind, checkfirst=True)

    _reconcile_deposits(bind)
    _rebalance_bank_defaults(bind)


def _reconcile_deposits(bind: sa.Connection) -> None:
    done = bind.execute(
        sa.text("SELECT deposits_reconciled FROM corporation WHERE id = 1")
    ).scalar()
    if done:
        return
    total = (
        bind.execute(
            sa.text("SELECT COALESCE(SUM(principal), 0) FROM deposits WHERE principal > 0")
        ).scalar()
        or 0
    )
    exists = bind.execute(sa.text("SELECT 1 FROM corporation WHERE id = 1")).scalar()
    if exists is None:
        bind.execute(
            sa.text(
                "INSERT INTO corporation (id, balance, total_tax, total_interest_earned, "
                "total_interest_paid, total_penalties, total_poker_rake, rules_url_rude, "
                "rules_url_strict, updated_at, deposits_reconciled, bank_rebalanced_v2) "
                "VALUES (1, :balance, 0, 0, 0, 0, 0, '', '', :now, 1, 0)"
            ),
            {"balance": int(total), "now": int(time.time())},
        )
    else:
        bind.execute(
            sa.text(
                "UPDATE corporation SET balance = balance + :balance, "
                "deposits_reconciled = 1 WHERE id = 1"
            ),
            {"balance": int(total)},
        )


def _rebalance_bank_defaults(bind: sa.Connection) -> None:
    done = bind.execute(sa.text("SELECT bank_rebalanced_v2 FROM corporation WHERE id = 1")).scalar()
    if done:
        return
    bind.execute(
        sa.text(
            "UPDATE global_settings SET dep_rate_pct = 2, dep_rate_decay_pct = 25, "
            "dep_rate_floor_pct = 0, dep_yield_cap_pct = 8 WHERE id = 1 "
            "AND dep_rate_pct = 3 AND dep_rate_decay_pct = 15 "
            "AND dep_rate_floor_pct = 1 AND dep_yield_cap_pct = 20"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE global_settings SET loan_rate_pct = 2, loan_min = 15, "
            "loan_term_days = 7 WHERE id = 1 AND loan_rate_pct = 5 "
            "AND loan_min = 5 AND loan_term_days = 5"
        )
    )
    bind.execute(sa.text("UPDATE corporation SET bank_rebalanced_v2 = 1 WHERE id = 1"))


def downgrade() -> None:
    raise RuntimeError("The adoption migration cannot be downgraded safely; restore its backup")
