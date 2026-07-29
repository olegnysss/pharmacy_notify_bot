"""Add versioned geographic scopes for subscriptions.

Revision ID: 20260729_14
Revises: 20260729_13
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_14"
down_revision: str | None = "20260729_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "location_scopes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("country_key", sa.String(length=128)),
        sa.Column("region_key", sa.String(length=128)),
        sa.Column("city_key", sa.String(length=128)),
        sa.Column("district_key", sa.String(length=128)),
        sa.Column("latitude", sa.Numeric(9, 6)),
        sa.Column("longitude", sa.Numeric(10, 6)),
        sa.Column("radius_meters", sa.Integer()),
        sa.Column("address_key", sa.String(length=128)),
        sa.Column("pharmacy_ids", sa.JSON(), nullable=False),
        sa.Column("online_region_key", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_location_scopes_version_positive"),
        sa.CheckConstraint(
            "kind IN ('country', 'region', 'city', 'district', 'radius', 'address', "
            "'pharmacy_list', 'online_region')",
            name="ck_location_scopes_kind",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_location_scopes_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_location_scopes_longitude",
        ),
        sa.CheckConstraint(
            "radius_meters IS NULL OR radius_meters > 0",
            name="ck_location_scopes_radius_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_location_scopes_fingerprint"),
    )
    op.create_table(
        "location_scope_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("location_scope_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("safe_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["location_scope_id"],
            ["location_scopes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "location_scope_id",
            "version",
            name="uq_location_scope_versions_scope_version",
        ),
    )
    for table_name in ("subscription_setup_drafts", "subscriptions"):
        op.add_column(table_name, sa.Column("location_scope_id", sa.BigInteger()))
        op.add_column(table_name, sa.Column("location_scope_version", sa.Integer()))
        op.create_foreign_key(
            f"fk_{table_name}_location_scope",
            table_name,
            "location_scopes",
            ["location_scope_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table_name in ("subscriptions", "subscription_setup_drafts"):
        op.drop_constraint(
            f"fk_{table_name}_location_scope",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "location_scope_version")
        op.drop_column(table_name, "location_scope_id")
    op.drop_table("location_scope_versions")
    op.drop_table("location_scopes")
