"""Create server-side product selection drafts.

Revision ID: 20260729_02
Revises: 20260729_01
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_02"
down_revision: str | None = "20260729_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_selection_drafts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_mode", sa.String(length=16), nullable=True),
        sa.Column("query_text", sa.String(length=512), nullable=True),
        sa.Column("source_host", sa.String(length=255), nullable=True),
        sa.Column("selected_ordinal", sa.Integer(), nullable=True),
        sa.Column("selected_candidate_version", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "product_selection_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("candidate_key", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("form", sa.String(length=128), nullable=True),
        sa.Column("dosage", sa.String(length=128), nullable=True),
        sa.Column("package", sa.String(length=128), nullable=True),
        sa.Column("manufacturer", sa.String(length=256), nullable=True),
        sa.Column("source_name", sa.String(length=128), nullable=True),
        sa.Column("source_host", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["product_selection_drafts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id",
            "ordinal",
            name="uq_product_selection_candidates_draft_ordinal",
        ),
    )


def downgrade() -> None:
    op.drop_table("product_selection_candidates")
    op.drop_table("product_selection_drafts")
