"""Stop registering private chats as game chats.

Adds ``users.dm_started_at`` as the "the user opened a DM" marker and backfills
it from the private chats that exist today. The private chat rows themselves are
removed separately, after this marker is in place.

Revision ID: 0013_dm_not_a_chat
Revises: 0012_corp_recovery_rule
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_dm_not_a_chat"
down_revision = "0012_corp_recovery_rule"
branch_labels = None
depends_on = None

COLUMN = "dm_started_at"

_BACKFILL = sa.text(
    "UPDATE users SET dm_started_at = COALESCE("
    "(SELECT c.updated_at FROM chats c WHERE c.chat_id = users.user_id "
    "AND c.type = 'private'), 0) "
    "WHERE dm_started_at = 0 AND EXISTS ("
    "SELECT 1 FROM chats c WHERE c.chat_id = users.user_id AND c.type = 'private')"
)


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "users" in tables and COLUMN not in _columns(bind, "users"):
        op.add_column(
            "users",
            sa.Column(COLUMN, sa.Integer(), nullable=False, server_default="0"),
        )

    if "users" in tables and "chats" in tables:
        bind.execute(_BACKFILL)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "users" in tables and COLUMN in _columns(bind, "users"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column(COLUMN)
