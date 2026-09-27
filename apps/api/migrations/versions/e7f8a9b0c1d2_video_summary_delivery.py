"""Video-level summary events and independent Webhook deliveries.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "push_channel",
        sa.Column("summary_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "video_summary_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("item_id", "version", name="uq_video_summary_event_item_version"),
    )
    op.create_index("ix_video_summary_event_item_id", "video_summary_event", ["item_id"])
    op.create_table(
        "video_summary_delivery",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("channel_id", sa.BigInteger(), sa.ForeignKey("push_channel.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("video_summary_event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("last_http_status", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("channel_id", "event_id", name="uq_video_summary_delivery_channel_event"),
    )
    op.create_index("ix_video_summary_delivery_channel_id", "video_summary_delivery", ["channel_id"])
    op.create_index("ix_video_summary_delivery_event_id", "video_summary_delivery", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_video_summary_delivery_event_id", table_name="video_summary_delivery")
    op.drop_index("ix_video_summary_delivery_channel_id", table_name="video_summary_delivery")
    op.drop_table("video_summary_delivery")
    op.drop_index("ix_video_summary_event_item_id", table_name="video_summary_event")
    op.drop_table("video_summary_event")
    op.drop_column("push_channel", "summary_enabled")
