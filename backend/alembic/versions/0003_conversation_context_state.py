"""Add bounded rolling context state to conversations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("context_summary", sa.Text(), nullable=True))
    op.add_column("conversations", sa.Column("context_summary_through_id", sa.String(36), nullable=True))
    op.add_column("conversations", sa.Column("context_summary_source_hash", sa.String(64), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("context_summary_token_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversations",
        sa.Column("context_summary_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("conversations", sa.Column("context_summary_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "context_summary_updated_at")
    op.drop_column("conversations", "context_summary_version")
    op.drop_column("conversations", "context_summary_token_count")
    op.drop_column("conversations", "context_summary_source_hash")
    op.drop_column("conversations", "context_summary_through_id")
    op.drop_column("conversations", "context_summary")
