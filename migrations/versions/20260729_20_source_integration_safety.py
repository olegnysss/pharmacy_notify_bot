"""Add webhook receipts and isolated source observability.

Revision ID: 20260729_20
Revises: 20260729_19
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_20"
down_revision: str | None = "20260729_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("delivery_key", sa.String(length=128), nullable=False),
        sa.Column("body_digest", sa.String(length=64), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("business_fingerprint", sa.String(length=64)),
        sa.Column("quarantine_reason", sa.String(length=64)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("body_bytes >= 0", name="ck_webhook_receipts_body_bytes"),
        sa.CheckConstraint(
            "status IN ('processing', 'accepted', 'quarantined')",
            name="ck_webhook_receipts_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "delivery_key",
            name="uq_webhook_receipts_source_delivery",
        ),
    )
    op.create_table(
        "integration_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("response_bytes", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("cache_status", sa.String(length=16)),
        sa.Column("failure_code", sa.String(length=64)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cache_status IS NULL OR cache_status IN ('fresh', 'stale', 'miss')",
            name="ck_integration_requests_cache_status",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0 AND attempts > 0 AND response_bytes >= 0",
            name="ck_integration_requests_metrics",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'client_failure', 'upstream_failure', "
            "'network_failure', 'contract_failure', 'policy_rejection')",
            name="ck_integration_requests_outcome",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "correlation_id",
            name="uq_integration_requests_source_correlation",
        ),
    )
    op.create_table(
        "source_health",
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_successes", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consecutive_failures >= 0 AND consecutive_successes >= 0 AND version > 0",
            name="ck_source_health_counters",
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'degraded')",
            name="ck_source_health_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_table(
        "source_health_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('healthy', 'degraded')",
            name="ck_source_health_events_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "version",
            name="uq_source_health_events_source_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("source_health_events")
    op.drop_table("source_health")
    op.drop_table("integration_requests")
    op.drop_table("webhook_receipts")
