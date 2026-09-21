"""viewpoint.entity_raw：抽取时的原始标的名（未归一到 entity 词典也可显示）

用户反馈：观点未显示标的——entities 只在命中归一词典时落 entity_id，
其余进 entity_candidate 无从展示。加原始名列，抽取时直写，列表直接展示。

Revision ID: a9b3c1d5e7f2
Revises: f7a8b9c0d1e2
Create Date: 2026-09-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b3c1d5e7f2"
down_revision: str | Sequence[str] | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("viewpoint", sa.Column("entity_raw", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("viewpoint", "entity_raw")
