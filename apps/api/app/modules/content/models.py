"""模型定义（统一架构：按数据归属归入拥有者模块）。

原 db/models/*.py 拆分迁入；表名、列、约束完全不变（兼容基线见 evidence/baseline-2）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.runtime.db.base import Base, IdMixin, TimestampMixin


class SourceItem(TimestampMixin, IdMixin, Base):
    __tablename__ = "source_item"
    __table_args__ = (
        UniqueConstraint(
            "source_account_id", "external_item_id", name="uq_source_item_account_external"
        ),
    )

    source_account_id: Mapped[int] = mapped_column(
        ForeignKey("source_account.id", ondelete="CASCADE"), nullable=False
    )
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False, default="vod")
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="discovered")
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # --- Plan #6 内容生命周期 ---
    # 精华资产：true = 永久保留（retention sweep 跳过）；false = 到期物理删除（留结论快照）
    is_asset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    asset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # --- 视频观点一句话总结（用户 2026-09-24）---
    # 待审观点清零（item → ready）后由 LLM 整合 confirmed 观点生成；开放 API 对外输出
    viewpoint_summary: Mapped[str | None] = mapped_column(Text)
    summary_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



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


class DeletedItemRef(Base):
    """物理删除条目的墓碑：防止 discover 下一轮把同一内容重新导入并再次自动转写。"""

    __tablename__ = "deleted_item_ref"
    __table_args__ = (
        UniqueConstraint("source_account_id", "external_item_id", name="uq_deleted_item_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_account_id: Mapped[int] = mapped_column(
        ForeignKey("source_account.id", ondelete="CASCADE"), nullable=False
    )
    external_item_id: Mapped[str] = mapped_column(String(200), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
