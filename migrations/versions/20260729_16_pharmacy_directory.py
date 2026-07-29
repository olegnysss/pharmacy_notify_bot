"""Add canonical and source pharmacy directory.

Revision ID: 20260729_16
Revises: 20260729_15
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_16"
down_revision: str | None = "20260729_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("normalized_address", sa.String(length=512), nullable=False),
        sa.Column("network_key", sa.String(length=128)),
        sa.Column("latitude", sa.Numeric(9, 6)),
        sa.Column("longitude", sa.Numeric(10, 6)),
        sa.Column("trusted_identifier", sa.String(length=128)),
    ]


def upgrade() -> None:
    op.create_table(
        "pharmacies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_pharmacies_version_positive"),
        sa.CheckConstraint("kind IN ('pharmacy', 'pickup_point')", name="ck_pharmacies_kind"),
        sa.CheckConstraint(
            "status IN ('active', 'temporarily_closed', 'retired')",
            name="ck_pharmacies_status",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_pharmacies_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_pharmacies_longitude",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_pharmacies_fingerprint"),
    )
    op.create_index(
        "ix_pharmacies_coordinates_status",
        "pharmacies",
        ["latitude", "longitude", "status"],
    )
    op.create_table(
        "pharmacy_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pharmacy_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("safe_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pharmacy_id"], ["pharmacies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pharmacy_id", "version", name="uq_pharmacy_versions_pharmacy_version"),
    )
    op.create_table(
        "source_pharmacies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.Column("canonical_pharmacy_id", sa.BigInteger()),
        sa.Column("mapping_level", sa.String(length=32)),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_source_pharmacies_version_positive"),
        sa.CheckConstraint(
            "mapping_version >= 0",
            name="ck_source_pharmacies_mapping_version_nonnegative",
        ),
        sa.CheckConstraint(
            "kind IN ('pharmacy', 'pickup_point')",
            name="ck_source_pharmacies_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'temporarily_closed', 'retired')",
            name="ck_source_pharmacies_status",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_source_pharmacies_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_source_pharmacies_longitude",
        ),
        sa.ForeignKeyConstraint(["canonical_pharmacy_id"], ["pharmacies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_code",
            "external_id",
            name="uq_source_pharmacies_source_external",
        ),
    )
    op.create_table(
        "source_pharmacy_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_pharmacy_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("safe_snapshot", sa.JSON(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_pharmacy_id"], ["source_pharmacies.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_pharmacy_id",
            "version",
            name="uq_source_pharmacy_versions_source_version",
        ),
    )
    op.create_table(
        "pharmacy_mapping_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_pharmacy_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_pharmacy_id", sa.BigInteger()),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("match_level", sa.String(length=32)),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("actor_internal_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('confirm', 'revoke')",
            name="ck_pharmacy_mapping_decisions_action",
        ),
        sa.ForeignKeyConstraint(
            ["source_pharmacy_id"], ["source_pharmacies.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["canonical_pharmacy_id"], ["pharmacies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_internal_id",
            "idempotency_key",
            name="uq_pharmacy_mapping_actor_idempotency",
        ),
    )
    op.create_index(
        "ix_pharmacy_mapping_source_created",
        "pharmacy_mapping_decisions",
        ["source_pharmacy_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pharmacy_mapping_source_created",
        table_name="pharmacy_mapping_decisions",
    )
    op.drop_table("pharmacy_mapping_decisions")
    op.drop_table("source_pharmacy_versions")
    op.drop_table("source_pharmacies")
    op.drop_table("pharmacy_versions")
    op.drop_index("ix_pharmacies_coordinates_status", table_name="pharmacies")
    op.drop_table("pharmacies")
