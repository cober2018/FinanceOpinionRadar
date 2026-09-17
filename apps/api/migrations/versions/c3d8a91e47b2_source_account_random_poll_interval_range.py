"""source_account 人类化随机轮询区间字段

Revision ID: c3d8a91e47b2
Revises: 07402f07249c
Create Date: 2026-09-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d8a91e47b2'
down_revision: str | Sequence[str] | None = '07402f07249c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """随机轮询区间（秒）：NULL = 保持固定 poll_interval_sec（旧行为兼容）。"""
    op.add_column('source_account', sa.Column('poll_interval_min_sec', sa.Integer(), nullable=True))
    op.add_column('source_account', sa.Column('poll_interval_max_sec', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('source_account', 'poll_interval_max_sec')
    op.drop_column('source_account', 'poll_interval_min_sec')
