"""Add dialog schema versions and Telegram update receipts.

Revision ID: 20260729_09
Revises: 20260729_08
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_09"
down_revision: str | None = "20260729_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in (
        "product_selection_drafts",
        "subscription_setup_drafts",
        "subscription_edit_drafts",
    ):
        op.add_column(
            table_name,
            sa.Column(
                "schema_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )
    op.add_column(
        "user_preferences",
        sa.Column(
            "editor_schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "user_preferences",
        sa.Column("editor_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "telegram_update_receipts",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_id", sa.String(length=36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("update_id"),
    )


def downgrade() -> None:
    op.drop_table("telegram_update_receipts")
    op.drop_column("user_preferences", "editor_expires_at")
    op.drop_column("user_preferences", "editor_schema_version")
    for table_name in (
        "subscription_edit_drafts",
        "subscription_setup_drafts",
        "product_selection_drafts",
    ):
        op.drop_column(table_name, "schema_version")
