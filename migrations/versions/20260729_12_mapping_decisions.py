"""Add scoped, auditable product mapping decisions.

Revision ID: 20260729_12
Revises: 20260729_11
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_12"
down_revision: str | None = "20260729_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mapping_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_product_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_product_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_product_version", sa.Integer(), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_internal_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_user_id", sa.BigInteger()),
        sa.Column("source_code", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("version > 0", name="ck_mapping_decisions_version_positive"),
        sa.CheckConstraint(
            "actor_type IN ('user', 'operator')",
            name="ck_mapping_decisions_actor_type",
        ),
        sa.CheckConstraint(
            "scope IN ('user', 'source', 'global')",
            name="ck_mapping_decisions_scope",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_mapping_decisions_status",
        ),
        sa.CheckConstraint(
            "(scope = 'user' AND scope_user_id IS NOT NULL) "
            "OR (scope <> 'user' AND scope_user_id IS NULL)",
            name="ck_mapping_decisions_scope_user",
        ),
        sa.ForeignKeyConstraint(
            ["source_product_id"],
            ["source_products.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_product_id"],
            ["canonical_products.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_type",
            "actor_internal_id",
            "idempotency_key",
            name="uq_mapping_decisions_actor_idempotency",
        ),
    )
    op.create_index(
        "ix_mapping_decisions_active_lookup",
        "mapping_decisions",
        ["source_product_id", "canonical_product_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mapping_decisions_active_lookup", table_name="mapping_decisions")
    op.drop_table("mapping_decisions")
