"""内容生命周期：source_item 精华标记 + content_summary 结论快照表（Plan #6）

Revision ID: f7a8b9c0d1e2
Revises: e6a1b2c3d4f5
Create Date: 2026-09-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "e6a1b2c3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """精华资产标记（true=永久保留，retention sweep 跳过）+ 结论快照表。"""
    op.add_column(
        "source_item",
        sa.Column("is_asset", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("source_item", sa.Column("asset_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "content_summary",
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("creator_name", sa.String(length=200), nullable=False),
        sa.Column("item_title", sa.Text(), nullable=True),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("item_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewpoints", JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_summary")),
    )
    op.create_index(
        "ix_content_summary_creator_time",
        "content_summary",
        ["creator_name", "item_created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_content_summary_creator_time", table_name="content_summary")
    op.drop_table("content_summary")
    op.drop_column("source_item", "asset_at")
    op.drop_column("source_item", "is_asset")
