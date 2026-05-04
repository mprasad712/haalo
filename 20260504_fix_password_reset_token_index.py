"""Fix password_reset_token index name to match SQLModel convention

Revision ID: 20260504_fix_prt_index
Revises: 20260504_password_reset_token
Create Date: 2026-05-04

Renames ix_password_reset_token_hash → ix_password_reset_token_token_hash
to match the auto-generated name SQLModel produces (ix_{table}_{column}).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260504_fix_prt_index"
down_revision: Union[str, Sequence[str], None] = "20260504_password_reset_token"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(bind, index_name: str) -> bool:
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :name"
        ),
        {"name": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()
    if _index_exists(bind, "ix_password_reset_token_hash"):
        op.drop_index("ix_password_reset_token_hash", table_name="password_reset_token")
    if not _index_exists(bind, "ix_password_reset_token_token_hash"):
        op.create_index(
            "ix_password_reset_token_token_hash",
            "password_reset_token",
            ["token_hash"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _index_exists(bind, "ix_password_reset_token_token_hash"):
        op.drop_index("ix_password_reset_token_token_hash", table_name="password_reset_token")
    if not _index_exists(bind, "ix_password_reset_token_hash"):
        op.create_index(
            "ix_password_reset_token_hash",
            "password_reset_token",
            ["token_hash"],
            unique=True,
        )
