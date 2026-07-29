"""Add subscription edit drafts and lifecycle audit.

Revision ID: 20260729_07
Revises: 20260729_06
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_07"
down_revision: str | None = "20260729_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_edit_drafts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("base_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location_mode", sa.String(length=32)),
        sa.Column("location_candidates", sa.JSON(), nullable=False),
        sa.Column("location", sa.JSON(), nullable=False),
        sa.Column("radius_meters", sa.Integer(), nullable=False),
        sa.Column("available_sources", sa.JSON(), nullable=False),
        sa.Column("selected_source_codes", sa.JSON(), nullable=False),
        sa.Column("notify_low_stock", sa.Boolean(), nullable=False),
        sa.Column("notify_orderable", sa.Boolean(), nullable=False),
        sa.Column("include_price", sa.Boolean(), nullable=False),
        sa.Column("completion_mode", sa.String(length=32), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_user_subscription_time",
        "audit_logs",
        ["user_id", "subscription_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_user_subscription_time", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("subscription_edit_drafts")
