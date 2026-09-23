"""开放数据层：api_key / api_call_log / push_channel / push_delivery（Plan #7）

Revision ID: b3c4d5e6f7a8
Revises: a9b3c1d5e7f2
Create Date: 2026-09-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a9b3c1d5e7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """底座对外输出：密钥 + 调用审计 + 推送渠道 + 投递日志。"""
    op.create_table(
        "api_key",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_key")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_key_key_hash")),
    )
    op.create_table(
        "api_call_log",
        sa.Column("api_key_id", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_call_log")),
    )
    op.create_index(
        "ix_api_call_log_api_key_id", "api_call_log", ["api_key_id"], unique=False
    )
    op.create_index(
        "ix_api_call_log_created_at", "api_call_log", ["created_at"], unique=False
    )
    op.create_table(
        "push_channel",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("channel_type", sa.String(length=30), nullable=False),
        sa.Column(
            "config_json", JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_channel")),
    )
    op.create_table(
        "push_delivery",
        sa.Column(
            "channel_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column("viewpoint_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_delivery")),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["push_channel.id"],
            name=op.f("fk_push_delivery_channel_id_push_channel"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_push_delivery_channel_id", "push_delivery", ["channel_id"], unique=False
    )
    op.create_index(
        "ix_push_delivery_viewpoint_id", "push_delivery", ["viewpoint_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_push_delivery_viewpoint_id", table_name="push_delivery")
    op.drop_index("ix_push_delivery_channel_id", table_name="push_delivery")
    op.drop_table("push_delivery")
    op.drop_table("push_channel")
    op.drop_index("ix_api_call_log_created_at", table_name="api_call_log")
    op.drop_index("ix_api_call_log_api_key_id", table_name="api_call_log")
    op.drop_table("api_call_log")
    op.drop_table("api_key")  # uq_api_key_key_hash 随表删除，无需单独 drop
