"""Add canonical product catalog and immutable identity versions.

Revision ID: 20260729_10
Revises: 20260729_09
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_10"
down_revision: str | None = "20260729_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canonical_products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("critical_signature", sa.String(length=64), nullable=False),
        sa.Column("trade_name_raw", sa.String(length=512), nullable=False),
        sa.Column("trade_name_normalized", sa.String(length=512), nullable=False),
        sa.Column("active_ingredient_raw", sa.String(length=512)),
        sa.Column("active_ingredient_normalized", sa.String(length=512)),
        sa.Column("manufacturer_raw", sa.String(length=512)),
        sa.Column("manufacturer_normalized", sa.String(length=512)),
        sa.Column("form_raw", sa.String(length=128)),
        sa.Column("form_normalized", sa.String(length=128)),
        sa.Column("dosage_value", sa.Numeric(24, 9)),
        sa.Column("dosage_unit", sa.String(length=16)),
        sa.Column("dosage_dimension", sa.String(length=16)),
        sa.Column("concentration_numerator_value", sa.Numeric(24, 9)),
        sa.Column("concentration_numerator_unit", sa.String(length=16)),
        sa.Column("concentration_numerator_dimension", sa.String(length=16)),
        sa.Column("concentration_denominator_value", sa.Numeric(24, 9)),
        sa.Column("concentration_denominator_unit", sa.String(length=16)),
        sa.Column("concentration_denominator_dimension", sa.String(length=16)),
        sa.Column("package_count", sa.Integer()),
        sa.Column("volume_value", sa.Numeric(24, 9)),
        sa.Column("volume_unit", sa.String(length=16)),
        sa.Column("volume_dimension", sa.String(length=16)),
        sa.Column("route_raw", sa.String(length=128)),
        sa.Column("route_normalized", sa.String(length=128)),
        sa.Column("package_variant_raw", sa.String(length=256)),
        sa.Column("package_variant_normalized", sa.String(length=256)),
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
        sa.CheckConstraint("version > 0", name="ck_canonical_products_version_positive"),
        sa.CheckConstraint(
            "package_count IS NULL OR package_count > 0",
            name="ck_canonical_products_package_count_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('medicine', 'other')",
            name="ck_canonical_products_kind",
        ),
        sa.CheckConstraint(
            "quality IN ('partial', 'verified', 'retired')",
            name="ck_canonical_products_quality",
        ),
        sa.CheckConstraint(
            "dosage_value IS NULL OR dosage_value > 0",
            name="ck_canonical_products_dosage_positive",
        ),
        sa.CheckConstraint(
            "concentration_numerator_value IS NULL OR concentration_numerator_value > 0",
            name="ck_canonical_products_concentration_numerator_positive",
        ),
        sa.CheckConstraint(
            "concentration_denominator_value IS NULL OR concentration_denominator_value > 0",
            name="ck_canonical_products_concentration_denominator_positive",
        ),
        sa.CheckConstraint(
            "volume_value IS NULL OR volume_value > 0",
            name="ck_canonical_products_volume_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "critical_signature",
            name="uq_canonical_products_signature",
        ),
    )
    op.create_index(
        "ix_canonical_products_trade_name",
        "canonical_products",
        ["trade_name_normalized"],
    )
    op.create_table(
        "product_identifiers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("namespace", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("issuer", sa.String(length=256), nullable=False),
        sa.Column("trust", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["canonical_products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace",
            "value",
            name="uq_product_identifiers_namespace_value",
        ),
    )
    op.create_index(
        "ix_product_identifiers_product_status",
        "product_identifiers",
        ["product_id", "status"],
    )
    op.create_table(
        "canonical_product_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("identity_snapshot", sa.JSON(), nullable=False),
        sa.Column("critical_signature", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["canonical_products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id",
            "version",
            name="uq_product_versions_product_version",
        ),
    )
    op.create_table(
        "product_attribute_provenance",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("product_version", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=False),
        sa.Column("raw_value", sa.String(length=1024)),
        sa.Column("normalized_value", sa.String(length=1024)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_version", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["canonical_products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_provenance_product_field",
        "product_attribute_provenance",
        ["product_id", "field_name"],
    )
    for table_name in ("subscription_setup_drafts", "subscriptions"):
        op.add_column(table_name, sa.Column("canonical_product_id", sa.BigInteger()))
        op.add_column(table_name, sa.Column("canonical_product_version", sa.Integer()))
        op.create_foreign_key(
            f"fk_{table_name}_canonical_product",
            table_name,
            "canonical_products",
            ["canonical_product_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table_name in ("subscriptions", "subscription_setup_drafts"):
        op.drop_constraint(
            f"fk_{table_name}_canonical_product",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "canonical_product_version")
        op.drop_column(table_name, "canonical_product_id")
    op.drop_index(
        "ix_product_provenance_product_field",
        table_name="product_attribute_provenance",
    )
    op.drop_table("product_attribute_provenance")
    op.drop_table("canonical_product_versions")
    op.drop_index(
        "ix_product_identifiers_product_status",
        table_name="product_identifiers",
    )
    op.drop_table("product_identifiers")
    op.drop_index("ix_canonical_products_trade_name", table_name="canonical_products")
    op.drop_table("canonical_products")
