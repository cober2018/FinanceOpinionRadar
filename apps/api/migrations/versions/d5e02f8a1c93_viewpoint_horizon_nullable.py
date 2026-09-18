"""viewpoint.horizon 改可空：观点未提时间维度是合法状态（MiniMax 实录崩溃修复）

Revision ID: d5e02f8a1c93
Revises: c8d41e6f9a27
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e02f8a1c93"
down_revision: str | Sequence[str] | None = "c8d41e6f9a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("viewpoint", "horizon", existing_type=sa.String(length=30), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE viewpoint SET horizon = '3M+' WHERE horizon IS NULL")
    op.alter_column("viewpoint", "horizon", existing_type=sa.String(length=30), nullable=False)
