"""source_item 补观点一句话总结两列（用户 2026-09-24）

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-09-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_item", sa.Column("viewpoint_summary", sa.Text(), nullable=True))
    op.add_column(
        "source_item",
        sa.Column("summary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_item", "summary_generated_at")
    op.drop_column("source_item", "viewpoint_summary")
