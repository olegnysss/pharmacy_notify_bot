"""Add safe generation-bound geocoding sessions.

Revision ID: 20260729_15
Revises: 20260729_14
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_15"
down_revision: str | None = "20260729_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "geocoding_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("region_hint_hash", sa.String(length=64)),
        sa.Column("provider_code", sa.String(length=64), nullable=False),
        sa.Column("provider_data_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("selected_candidate_id", sa.String(length=24)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('exact', 'ambiguous', 'insufficient', 'confirmed')",
            name="ck_geocoding_sessions_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "generation",
            name="uq_geocoding_sessions_user_generation",
        ),
    )


def downgrade() -> None:
    op.drop_table("geocoding_sessions")
