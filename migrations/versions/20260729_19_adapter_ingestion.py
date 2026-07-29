"""Add idempotent adapter ingestion receipts.

Revision ID: 20260729_19
Revises: 20260729_18
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_19"
down_revision: str | None = "20260729_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "adapter_ingestion_receipts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("result_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("causation_id", sa.String(length=36)),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("safe_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('health', 'search_products', 'get_product', "
            "'list_pharmacies', 'check_availability')",
            name="ck_adapter_receipts_operation",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "idempotency_key",
            name="uq_adapter_receipts_source_idempotency",
        ),
    )


def downgrade() -> None:
    op.drop_table("adapter_ingestion_receipts")
