"""Add subscription freshness and manual-check state.

Revision ID: 20260729_06
Revises: 20260729_05
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_06"
down_revision: str | None = "20260729_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("state_updated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "subscriptions",
        sa.Column("last_successful_check_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "subscriptions",
        sa.Column("freshness_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "subscriptions",
        sa.Column("state_source_name", sa.String(length=128)),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "has_partial_source_error",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "manual_check_in_progress",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("next_manual_check_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "next_manual_check_at")
    op.drop_column("subscriptions", "manual_check_in_progress")
    op.drop_column("subscriptions", "has_partial_source_error")
    op.drop_column("subscriptions", "state_source_name")
    op.drop_column("subscriptions", "freshness_expires_at")
    op.drop_column("subscriptions", "last_successful_check_at")
    op.drop_column("subscriptions", "state_updated_at")
