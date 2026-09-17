"""source_account live_room_url 列 + app_setting 运行时配置表

Revision ID: b7e4d2c9a5f1
Revises: c3d8a91e47b2
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision: str = "b7e4d2c9a5f1"
down_revision: str | Sequence[str] | None = "c3d8a91e47b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """live_room_url：值守直播间与发现主页分离（一个博主可同时配两路，解 URL 身份冲突挂账）。

    回填：douyin 账号 url 已是直播间形态的（桥契约临时变通过渡态）→ 迁入 live_room_url。
    """
    op.add_column("source_account", sa.Column("live_room_url", sa.Text(), nullable=True))
    op.execute(
        "UPDATE source_account SET live_room_url = url "
        "WHERE platform = 'douyin' AND url LIKE '%live.douyin.com/%'"
    )
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
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
    )


def downgrade() -> None:
    op.drop_table("app_setting")
    op.drop_column("source_account", "live_room_url")
