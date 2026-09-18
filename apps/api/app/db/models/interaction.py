"""互动侧模型（Plan #5）：观众语料，舆论分析的观众侧输入。

与 transcript_segment（主播语音转写）严格分离：本表存直播观众的弹幕/礼物/点赞/
进场/关注事件，不参与 EPIC-04 的博主内容抽取链路。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class LiveChatMessage(TimestampMixin, IdMixin, Base):
    """单条直播弹幕事件（douyinLive WS 推送的原始 method 一条一行）。

    幂等键 (source_item_id, external_msg_id)：采集器崩溃重派 → jsonl 追加重复行 →
    ingest ON CONFLICT DO NOTHING 去重。服务端原文留 jsonl 文件（F3），库内只存类型化列。
    """

    __tablename__ = "live_chat_message"
    __table_args__ = (
        UniqueConstraint("source_item_id", "external_msg_id", name="uq_live_chat_item_msg"),
        Index("ix_live_chat_item_published", "source_item_id", "published_at"),
    )

    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, default="douyin")
    # 抖音原始 method（WebcastChatMessage/WebcastGiftMessage/...）
    msg_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # common.msgId；缺失时解析器确定性合成（F5），重放稳定
    external_msg_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(64))
    user_name: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str | None] = mapped_column(Text)
    gift_name: Mapped[str | None] = mapped_column(String(200))
    repeat_count: Mapped[int | None] = mapped_column(Integer)
    like_count: Mapped[int | None] = mapped_column(Integer)
    member_count: Mapped[int | None] = mapped_column(Integer)
    # common.createTime（毫秒 epoch → UTC）；解析失败为 None，不阻断入库
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["LiveChatMessage"]
