"""Create password_reset_token table

Revision ID: 20260504_password_reset_token
Revises: 20260317_merge_all
Create Date: 2026-05-04

Single-use password reset tokens for the "forgot password" flow.
Only the SHA-256 hash of the raw token is stored here; the raw token
is sent to the user via email only. A token is consumed by setting
used_at, and expires after 30 minutes via expires_at.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_password_reset_token"
down_revision: Union[str, Sequence[str], None] = "20260317_merge_all"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_reset_token",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_password_reset_token_user_id_user",
        ),
    )
    op.create_index(
        "ix_password_reset_token_hash",
        "password_reset_token",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_token_user_id",
        "password_reset_token",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_token_user_id", table_name="password_reset_token")
    op.drop_index("ix_password_reset_token_hash", table_name="password_reset_token")
    op.drop_constraint(
        "fk_password_reset_token_user_id_user",
        "password_reset_token",
        type_="foreignkey",
    )
    op.drop_table("password_reset_token")
