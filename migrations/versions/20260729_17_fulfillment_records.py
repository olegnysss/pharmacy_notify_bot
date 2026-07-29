"""Add typed fulfillment records.

Revision ID: 20260729_17
Revises: 20260729_16
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_17"
down_revision: str | None = "20260729_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fulfillment_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_product_id", sa.BigInteger(), nullable=False),
        sa.Column("fulfillment_type", sa.String(length=32), nullable=False),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("pharmacy_id", sa.BigInteger()),
        sa.Column("latitude", sa.Numeric(9, 6)),
        sa.Column("longitude", sa.Numeric(10, 6)),
        sa.Column("delivery_region_key", sa.String(length=128)),
        sa.Column("delivery_city_key", sa.String(length=128)),
        sa.Column("reference_key", sa.String(length=320), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_fulfillment_records_version_positive"),
        sa.CheckConstraint(
            "fulfillment_type IN ('physical_stock', 'pickup', 'delivery', 'online_unknown')",
            name="ck_fulfillment_records_type",
        ),
        sa.CheckConstraint(
            "(fulfillment_type IN ('physical_stock', 'pickup') "
            "AND pharmacy_id IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND delivery_region_key IS NULL AND delivery_city_key IS NULL) "
            "OR (fulfillment_type = 'delivery' AND pharmacy_id IS NULL "
            "AND latitude IS NULL AND longitude IS NULL "
            "AND (delivery_region_key IS NOT NULL OR delivery_city_key IS NOT NULL)) "
            "OR (fulfillment_type = 'online_unknown' AND pharmacy_id IS NULL "
            "AND latitude IS NULL AND longitude IS NULL "
            "AND delivery_region_key IS NULL AND delivery_city_key IS NULL)",
            name="ck_fulfillment_records_references",
        ),
        sa.ForeignKeyConstraint(["source_product_id"], ["source_products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pharmacy_id"], ["pharmacies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_product_id",
            "fulfillment_type",
            "reference_key",
            name="uq_fulfillment_source_type_reference",
        ),
    )


def downgrade() -> None:
    op.drop_table("fulfillment_records")
