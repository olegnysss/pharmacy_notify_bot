"""Allow validated product URLs in selection drafts.

Revision ID: 20260729_03
Revises: 20260729_02
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_03"
down_revision: str | None = "20260729_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "product_selection_drafts",
        "query_text",
        existing_type=sa.String(length=512),
        type_=sa.String(length=4096),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "product_selection_drafts",
        "query_text",
        existing_type=sa.String(length=4096),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
