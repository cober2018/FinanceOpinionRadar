"""live_chat_message 弹幕表（Plan #5：直播观众语料）

Revision ID: e6a1b2c3d4f5
Revises: d5e02f8a1c93
Create Date: 2026-09-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "e6a1b2c3d4f5"
down_revision: str | Sequence[str] | None = "d5e02f8a1c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """弹幕事件表：幂等键 (source_item_id, external_msg_id)，级联随 source_item 删除。"""
    op.create_table(
        "live_chat_message",
        sa.Column("source_item_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("msg_type", sa.String(length=60), nullable=False),
        sa.Column("external_msg_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("user_name", sa.String(length=200), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("gift_name", sa.String(length=200), nullable=True),
        sa.Column("repeat_count", sa.Integer(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["source_item.id"],
            name=op.f("fk_live_chat_message_source_item_id_source_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_live_chat_message")),
        sa.UniqueConstraint(
            "source_item_id", "external_msg_id", name="uq_live_chat_item_msg"
        ),
    )
    op.create_index(
        "ix_live_chat_item_published",
        "live_chat_message",
        ["source_item_id", "published_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_live_chat_item_published", table_name="live_chat_message")
    op.drop_table("live_chat_message")
