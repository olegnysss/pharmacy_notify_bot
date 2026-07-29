"""Add subscription setup drafts and subscriptions.

Revision ID: 20260729_04
Revises: 20260729_03
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_04"
down_revision: str | None = "20260729_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_setup_drafts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("product_candidate_key", sa.String(length=256), nullable=False),
        sa.Column("product_version", sa.String(length=128), nullable=False),
        sa.Column("product_name", sa.String(length=512), nullable=False),
        sa.Column("product_form", sa.String(length=128)),
        sa.Column("product_dosage", sa.String(length=128)),
        sa.Column("product_package", sa.String(length=128)),
        sa.Column("product_manufacturer", sa.String(length=256)),
        sa.Column("product_source_host", sa.String(length=255)),
        sa.Column("location_mode", sa.String(length=32)),
        sa.Column("location_candidates", sa.JSON(), nullable=False),
        sa.Column("location", sa.JSON()),
        sa.Column("radius_meters", sa.Integer()),
        sa.Column("available_sources", sa.JSON(), nullable=False),
        sa.Column("selected_source_codes", sa.JSON(), nullable=False),
        sa.Column("notify_low_stock", sa.Boolean(), nullable=False),
        sa.Column("notify_orderable", sa.Boolean(), nullable=False),
        sa.Column("include_price", sa.Boolean(), nullable=False),
        sa.Column("completion_mode", sa.String(length=32)),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("setup_draft_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("creation_key", sa.String(length=64), nullable=False),
        sa.Column("product_candidate_key", sa.String(length=256), nullable=False),
        sa.Column("product_version", sa.String(length=128), nullable=False),
        sa.Column("product_name", sa.String(length=512), nullable=False),
        sa.Column("product_form", sa.String(length=128)),
        sa.Column("product_dosage", sa.String(length=128)),
        sa.Column("product_package", sa.String(length=128)),
        sa.Column("product_manufacturer", sa.String(length=256)),
        sa.Column("product_source_host", sa.String(length=255)),
        sa.Column("location_kind", sa.String(length=32), nullable=False),
        sa.Column("location_key", sa.String(length=256), nullable=False),
        sa.Column("location_display_name", sa.String(length=512), nullable=False),
        sa.Column("location_city", sa.String(length=256)),
        sa.Column("location_address", sa.String(length=512)),
        sa.Column("location_latitude", sa.Float()),
        sa.Column("location_longitude", sa.Float()),
        sa.Column("radius_meters", sa.Integer(), nullable=False),
        sa.Column("source_codes", sa.JSON(), nullable=False),
        sa.Column("notify_low_stock", sa.Boolean(), nullable=False),
        sa.Column("notify_orderable", sa.Boolean(), nullable=False),
        sa.Column("include_price", sa.Boolean(), nullable=False),
        sa.Column("completion_mode", sa.String(length=32), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("availability_state", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["setup_draft_id"],
            ["subscription_setup_drafts.id"],
            name="subscriptions_setup_draft_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("creation_key"),
        sa.UniqueConstraint(
            "setup_draft_id",
            name="subscriptions_setup_draft_id_key",
        ),
    )
    op.create_index(
        "ix_subscriptions_user_status_created",
        "subscriptions",
        ["user_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_user_status_created", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("subscription_setup_drafts")
