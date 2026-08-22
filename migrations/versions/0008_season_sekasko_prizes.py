"""Award persistent SЕКАСКО coverage to weekly season leaders.

Revision ID: 0008_season_sekasko_prizes
Revises: 0007_weekly_seasons
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.interfaces import ReflectedColumn

revision = "0008_season_sekasko_prizes"
down_revision = "0007_weekly_seasons"
branch_labels = None
depends_on = None


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind: sa.Connection, table: str) -> set[str]:
    return {
        name for index in sa.inspect(bind).get_indexes(table) if (name := index["name"]) is not None
    }


def _server_default(column: ReflectedColumn) -> str:
    return str(column.get("default") or "").strip("()'\" ")


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "global_settings" in tables:
        existing = _columns(bind, "global_settings")
        global_defaults = {
            "dick_emission_cap": "2",
            "sekasko_max_coverage": "100",
            "dep_risk_free_principal": "50",
            "season_sekasko_prize_places": "3",
            "season_sekasko_prize_coverage": "10",
        }
        for name in (
            "dep_risk_free_principal",
            "season_sekasko_prize_places",
            "season_sekasko_prize_coverage",
        ):
            if name not in existing:
                op.add_column(
                    "global_settings",
                    sa.Column(
                        name,
                        sa.Integer(),
                        nullable=False,
                        server_default=global_defaults[name],
                    ),
                )
        # Forty was the old hardcoded default. Move that default to 100, cap
        # over-large custom values, and preserve other intentional lower values.
        bind.execute(
            sa.text(
                "UPDATE global_settings SET sekasko_max_coverage = "
                "CASE WHEN sekasko_max_coverage = 40 THEN 100 "
                "WHEN sekasko_max_coverage > 100 THEN 100 "
                "ELSE sekasko_max_coverage END"
            )
        )
        bind.execute(
            sa.text("UPDATE global_settings SET dick_emission_cap = 2 WHERE dick_emission_cap = 3")
        )
        columns = {
            column["name"]: column for column in sa.inspect(bind).get_columns("global_settings")
        }
        altered_defaults = {
            name: default
            for name, default in global_defaults.items()
            if _server_default(columns[name]) != default
        }
        if altered_defaults:
            with op.batch_alter_table("global_settings") as batch_op:
                for name, default in altered_defaults.items():
                    batch_op.alter_column(
                        name,
                        existing_type=sa.Integer(),
                        existing_nullable=False,
                        server_default=default,
                    )

    if "deposit_insurance" in tables:
        existing = _columns(bind, "deposit_insurance")
        if "source" not in existing:
            op.add_column(
                "deposit_insurance",
                sa.Column(
                    "source", sa.String(length=32), nullable=False, server_default="purchase"
                ),
            )
        if "source_season_id" not in existing:
            op.add_column(
                "deposit_insurance",
                sa.Column("source_season_id", sa.Integer(), nullable=True),
            )
        source_column = next(
            column
            for column in sa.inspect(bind).get_columns("deposit_insurance")
            if column["name"] == "source"
        )
        if _server_default(source_column) != "purchase":
            with op.batch_alter_table("deposit_insurance") as batch_op:
                batch_op.alter_column(
                    "source",
                    existing_type=sa.String(length=32),
                    existing_nullable=False,
                    server_default="purchase",
                )
        if "uq_deposit_insurance_season_user" not in _indexes(bind, "deposit_insurance"):
            op.create_index(
                "uq_deposit_insurance_season_user",
                "deposit_insurance",
                ["source_season_id", "user_id"],
                unique=True,
            )

    if "weekly_season_players" in tables:
        existing = _columns(bind, "weekly_season_players")
        for name in (
            "sekasko_prize_rank",
            "sekasko_prize_amount",
            "sekasko_prize_expires_at",
        ):
            if name not in existing:
                op.add_column(
                    "weekly_season_players",
                    sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
                )
        columns = {
            column["name"]: column
            for column in sa.inspect(bind).get_columns("weekly_season_players")
        }
        missing_defaults = tuple(
            name
            for name in (
                "sekasko_prize_rank",
                "sekasko_prize_amount",
                "sekasko_prize_expires_at",
            )
            if _server_default(columns[name]) != "0"
        )
        if missing_defaults:
            with op.batch_alter_table("weekly_season_players") as batch_op:
                for name in missing_defaults:
                    batch_op.alter_column(
                        name,
                        existing_type=sa.Integer(),
                        existing_nullable=False,
                        server_default="0",
                    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "weekly_season_players" in tables:
        existing = _columns(bind, "weekly_season_players")
        for name in (
            "sekasko_prize_expires_at",
            "sekasko_prize_amount",
            "sekasko_prize_rank",
        ):
            if name in existing:
                op.drop_column("weekly_season_players", name)

    if "deposit_insurance" in tables:
        if "uq_deposit_insurance_season_user" in _indexes(bind, "deposit_insurance"):
            op.drop_index("uq_deposit_insurance_season_user", table_name="deposit_insurance")
        existing = _columns(bind, "deposit_insurance")
        for name in ("source_season_id", "source"):
            if name in existing:
                op.drop_column("deposit_insurance", name)

    if "global_settings" in tables:
        existing = _columns(bind, "global_settings")
        removable = (
            "season_sekasko_prize_coverage",
            "season_sekasko_prize_places",
            "dep_risk_free_principal",
        )
        columns = {
            column["name"]: column for column in sa.inspect(bind).get_columns("global_settings")
        }
        restore_defaults = {
            "dick_emission_cap": "3",
            "sekasko_max_coverage": "40",
        }
        needs_restore = {
            name: default
            for name, default in restore_defaults.items()
            if name in columns and _server_default(columns[name]) != default
        }
        if needs_restore or any(name in existing for name in removable):
            with op.batch_alter_table("global_settings") as batch_op:
                for name, default in needs_restore.items():
                    batch_op.alter_column(
                        name,
                        existing_type=sa.Integer(),
                        existing_nullable=False,
                        server_default=default,
                    )
                for name in removable:
                    if name in existing:
                        batch_op.drop_column(name)
