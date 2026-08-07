"""Local corporations, SЕКАСКО and honest credit history.

Revision ID: 0004_local_corporations
Revises: 0003_pisyago_insurance
Create Date: 2026-08-08
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

revision = "0004_local_corporations"
down_revision = "0003_pisyago_insurance"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "players" in tables:
        if "last_credit_reward_at" not in _columns(bind, "players"):
            op.add_column(
                "players",
                sa.Column(
                    "last_credit_reward_at", sa.Integer(), nullable=False, server_default="0"
                ),
            )
        bind.execute(sa.text("UPDATE players SET loans_repaid = MIN(loans_repaid, 2)"))

    if "loans" in tables:
        existing = _columns(bind, "loans")
        for name, typ, default in (
            ("original_cash_principal", sa.Integer(), "0"),
            ("credit_limit_at_open", sa.Integer(), "0"),
            ("rating_eligible", sa.Boolean(), sa.false()),
        ):
            if name not in existing:
                op.add_column("loans", sa.Column(name, typ, nullable=False, server_default=default))

    if "global_settings" in tables:
        existing = _columns(bind, "global_settings")
        for name, default in (
            ("dick_emission_cap", "3"),
            ("corp_liquidity_reserve_pct", "25"),
            ("corp_sanation_days", "7"),
            ("sekasko_max_coverage", "40"),
            ("sekasko_premium_pct", "5"),
            ("credit_reward_min_age_days", "3"),
            ("credit_reward_cooldown_days", "14"),
            ("credit_reward_min_limit_pct", "25"),
        ):
            if name not in existing:
                op.add_column(
                    "global_settings",
                    sa.Column(name, sa.Integer(), nullable=False, server_default=default),
                )

    if "chat_corporations" not in tables:
        op.create_table(
            "chat_corporations",
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("insurance_reserve", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(), nullable=False, server_default="healthy"),
            sa.Column("sanation_started_at", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sanation_deadline", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("bankruptcy_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("crisis_message_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("crisis_thread_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_crisis_notice_at", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tax", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_interest_earned", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_interest_paid", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_penalties", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_poker_rake", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_emission", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_bailin", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["chat_id"], ["chats.chat_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("chat_id"),
        )
    if "deposit_insurance" not in tables:
        op.create_table(
            "deposit_insurance",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("premium", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_deposit_insurance_chat_id", "deposit_insurance", ["chat_id"])
        op.create_index("ix_deposit_insurance_user_id", "deposit_insurance", ["user_id"])

    # Split the legacy till without duplicating a single centimetre. Deposits are
    # the primary weight; active players are the deterministic zero-deposit fallback.
    legacy = bind.execute(sa.text("SELECT balance FROM corporation WHERE id = 1")).scalar()
    legacy_balance = int(legacy or 0)
    weights = list(
        bind.execute(
            sa.text(
                "SELECT chat_id, SUM(principal) AS weight FROM deposits "
                "WHERE principal > 0 GROUP BY chat_id ORDER BY chat_id"
            )
        )
    )
    if not weights:
        cutoff = int(time.time()) - 30 * 86400
        weights = list(
            bind.execute(
                sa.text(
                    "SELECT chat_id, COUNT(*) AS weight FROM players WHERE last_play >= :cutoff "
                    "GROUP BY chat_id ORDER BY chat_id"
                ),
                {"cutoff": cutoff},
            )
        )
    total_weight = sum(int(row.weight) for row in weights)
    allocations: list[tuple[int, int]] = []
    if total_weight > 0:
        sign = -1 if legacy_balance < 0 else 1
        absolute = abs(legacy_balance)
        used = 0
        for row in weights:
            share = absolute * int(row.weight) // total_weight
            allocations.append((int(row.chat_id), sign * share))
            used += share
        remainder = absolute - used
        for index in range(remainder):
            chat_id, share = allocations[index % len(allocations)]
            allocations[index % len(allocations)] = (chat_id, share + sign)
    now = int(time.time())
    for chat_id, balance in allocations:
        bind.execute(
            sa.text(
                "INSERT INTO chat_corporations (chat_id, balance, insurance_reserve, status, "
                "sanation_started_at, sanation_deadline, bankruptcy_count, crisis_message_id, "
                "crisis_thread_id, last_crisis_notice_at, total_tax, total_interest_earned, "
                "total_interest_paid, total_penalties, total_poker_rake, total_emission, "
                "total_bailin, updated_at) VALUES (:chat_id, :balance, 0, 'healthy', 0, 0, 0, "
                "0, 0, 0, 0, 0, 0, 0, 0, 0, 0, :now)"
            ),
            {"chat_id": chat_id, "balance": balance, "now": now},
        )
    if allocations:
        bind.execute(sa.text("UPDATE corporation SET balance = 0 WHERE id = 1"))


def downgrade() -> None:
    raise RuntimeError("Local economy migration is irreversible; restore its backup")
