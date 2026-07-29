"""Allow a setup draft to create later independent subscriptions.

Revision ID: 20260729_05
Revises: 20260729_04
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260729_05"
down_revision: str | None = "20260729_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_setup_draft_id_fkey"
    )
    op.execute(
        "ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS subscriptions_setup_draft_id_key"
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "subscriptions_setup_draft_id_key",
        "subscriptions",
        ["setup_draft_id"],
    )
    op.create_foreign_key(
        "subscriptions_setup_draft_id_fkey",
        "subscriptions",
        "subscription_setup_drafts",
        ["setup_draft_id"],
        ["id"],
        ondelete="RESTRICT",
    )
