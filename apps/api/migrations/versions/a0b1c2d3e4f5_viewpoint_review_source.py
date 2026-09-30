"""viewpoint 加复核来源与 LLM 复核意见两列（EPIC-05 LLM reviewer，2026-09-30）

review_source: rule|llm|manual——该条观点进入当前 verification_status 的裁决来源；
review_note: LLM/规则复核留下的裁决理由，人工复核队列直接可见。

Revision ID: a0b1c2d3e4f5
Revises: e7f8a9b0c1d2
Create Date: 2026-09-30

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "a0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "viewpoint", sa.Column("review_source", sa.String(length=20), nullable=True)
    )
    op.add_column("viewpoint", sa.Column("review_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("viewpoint", "review_note")
    op.drop_column("viewpoint", "review_source")
