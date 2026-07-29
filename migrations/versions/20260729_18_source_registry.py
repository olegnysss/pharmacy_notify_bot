"""Add versioned source registry and capability policy.

Revision ID: 20260729_18
Revises: 20260729_17
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_18"
down_revision: str | None = "20260729_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("legal_status", sa.String(length=32), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("capability_version", sa.String(length=64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("base_urls", sa.JSON(), nullable=False),
        sa.Column("redirect_hosts", sa.JSON(), nullable=False),
        sa.Column("requests_per_window", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("freshness_seconds", sa.Integer(), nullable=False),
        sa.Column("cache_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_sources_version_positive"),
        sa.CheckConstraint(
            "source_type IN "
            "('partner_api', 'public_api', 'webhook', 'export', 'public_page', 'manual')",
            name="ck_sources_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'degraded')",
            name="ck_sources_status",
        ),
        sa.CheckConstraint(
            "legal_status IN ('allowed', 'review_required', 'blocked')",
            name="ck_sources_legal_status",
        ),
        sa.CheckConstraint(
            "requests_per_window > 0 AND window_seconds > 0 AND max_concurrency > 0 "
            "AND freshness_seconds > 0 AND cache_ttl_seconds >= 0 "
            "AND cache_ttl_seconds <= freshness_seconds",
            name="ck_sources_limits_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )
    op.create_table(
        "source_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("safe_snapshot", sa.JSON(), nullable=False),
        sa.Column("actor_internal_id", sa.BigInteger()),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "version",
            name="uq_source_versions_source_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("source_versions")
    op.drop_table("sources")
