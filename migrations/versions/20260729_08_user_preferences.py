"""Add user defaults and notification preferences.

Revision ID: 20260729_08
Revises: 20260729_07
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260729_08"
down_revision: str | None = "20260729_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="ru"),
        sa.Column(
            "timezone_name",
            sa.String(length=64),
            nullable=False,
            server_default="Europe/Moscow",
        ),
        sa.Column("default_location", sa.JSON()),
        sa.Column("default_radius_meters", sa.Integer()),
        sa.Column(
            "default_source_codes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("notify_low_stock", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_orderable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("include_price", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "completion_mode",
            sa.String(length=32),
            nullable=False,
            server_default="continue",
        ),
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("quiet_hours_start", sa.Time(), nullable=False, server_default="22:00:00"),
        sa.Column("quiet_hours_end", sa.Time(), nullable=False, server_default="08:00:00"),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "max_points_per_message",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
        sa.Column(
            "editor_status",
            sa.String(length=32),
            nullable=False,
            server_default="idle",
        ),
        sa.Column("editor_location_mode", sa.String(length=32)),
        sa.Column(
            "editor_location_candidates",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
