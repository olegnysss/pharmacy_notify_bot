"""Add source revalidation, quarantine, and delivery eligibility state.

Revision ID: 20260729_13
Revises: 20260729_12
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_13"
down_revision: str | None = "20260729_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_products",
        sa.Column(
            "monitoring_eligibility",
            sa.String(length=32),
            nullable=False,
            server_default="pending_revalidation",
        ),
    )
    op.add_column(
        "source_products",
        sa.Column("quarantine_reason", sa.String(length=128)),
    )
    op.add_column(
        "source_products",
        sa.Column("quarantined_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "source_products",
        sa.Column(
            "last_revalidated_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "source_products",
        sa.Column("last_revalidated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "source_products",
        sa.Column(
            "fresh_check_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "ck_source_products_monitoring_eligibility",
        "source_products",
        "monitoring_eligibility IN "
        "('pending_revalidation', 'eligible', 'quarantined', 'awaiting_fresh_check')",
    )
    op.create_table(
        "source_product_revalidations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_product_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("drift_class", sa.String(length=32), nullable=False),
        sa.Column("match_level", sa.String(length=32), nullable=False),
        sa.Column("match_confirmed", sa.Boolean(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("match_algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("safe_evidence", sa.JSON(), nullable=False),
        sa.Column("actor_type", sa.String(length=32)),
        sa.Column("actor_internal_id", sa.BigInteger()),
        sa.Column("reason_code", sa.String(length=128)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_product_id"],
            ["source_products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_product_id",
            "source_version",
            "algorithm_version",
            name="uq_source_revalidations_product_version_algorithm",
        ),
    )
    op.create_index(
        "ix_source_revalidations_product_created",
        "source_product_revalidations",
        ["source_product_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_revalidations_product_created",
        table_name="source_product_revalidations",
    )
    op.drop_table("source_product_revalidations")
    op.drop_constraint(
        "ck_source_products_monitoring_eligibility",
        "source_products",
        type_="check",
    )
    op.drop_column("source_products", "fresh_check_required")
    op.drop_column("source_products", "last_revalidated_at")
    op.drop_column("source_products", "last_revalidated_version")
    op.drop_column("source_products", "quarantined_at")
    op.drop_column("source_products", "quarantine_reason")
    op.drop_column("source_products", "monitoring_eligibility")
