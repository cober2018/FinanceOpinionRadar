"""backfill youtube channel urls

Revision ID: backfill_channel_urls
Revises: 274c026452c4
Create Date: 2026-09-17 09:54:16.960224

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'backfill_channel_urls'
down_revision: str | Sequence[str] | None = '274c026452c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """注记③存量补扫：YouTube 裸频道地址补 /videos（注册侧已由 normalize_channel_url 兜新数据）。

    与 Python 侧 normalize_channel_url 同规则：裸频道形态（可带尾斜杠）才追加；
    /videos、/streams、/watch、list= 等页签/内容页因 [\\w.-] 不匹配 / 被正则天然排除。
    """
    op.execute(
        r"""
        UPDATE source_account
        SET url = rtrim(url, '/') || '/videos', updated_at = now()
        WHERE platform = 'youtube'
          AND url IS NOT NULL
          AND url ~ 'youtube\.com/(channel/UC[\w-]{20,}|@[\w.\-]+|c/[\w.\-]+|user/[\w.\-]+)/?$'
        """
    )


def downgrade() -> None:
    """一次性数据修复，不回滚（裸地址形态可由 /videos 再规范化得到，无信息损失）。"""
