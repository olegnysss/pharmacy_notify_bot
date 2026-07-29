"""Add external source products and semantic change history.

Revision ID: 20260729_11
Revises: 20260729_10
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_11"
down_revision: str | None = "20260729_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("raw_name", sa.String(length=512), nullable=False),
        sa.Column("parsed_attributes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("search_document", sa.String(length=2048), nullable=False),
        sa.Column("canonical_product_id", sa.BigInteger()),
        sa.Column("canonical_product_version", sa.Integer()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_source_products_version_positive"),
        sa.CheckConstraint(
            "status IN ('active', 'discontinued', 'unavailable')",
            name="ck_source_products_status",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_product_id"],
            ["canonical_products.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_code",
            "external_id",
            name="uq_source_products_source_external",
        ),
    )
    op.create_index(
        "ix_source_products_search",
        "source_products",
        ["id", "status"],
    )
    op.create_table(
        "source_product_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_product_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("safe_snapshot", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_product_id"],
            ["source_products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_product_id",
            "version",
            name="uq_source_product_versions_product_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("source_product_versions")
    op.drop_index("ix_source_products_search", table_name="source_products")
    op.drop_table("source_products")
