"""来源侧模型。

JSONB 形状（docstring-only，正式 schema 推迟 EPIC-04，见 ADR/M4）：
- source_account.config_json: 平台私有轮询配置（间隔覆盖、cookies 引用等），键集由 Adapter 定义。
- source_item.metadata_json: 平台原始元数据快照（标题/时长/统计数等），仅审计用途。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class SourceAccount(TimestampMixin, IdMixin, Base):
    __tablename__ = "source_account"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_source_account_platform_external"),
    )

    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creator.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text)
    discovery_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    poll_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # --- Plan #4 直播值守（RAD-LIVE-04）---
    # true 时 recorder_bridge 将该账号同步到 StreamCap 值守；enabled 仍为总开关
    live_monitor_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # 值守录制分片间隔（秒），与发现轮询间隔 poll_interval_sec 语义不同（README 字段速查表）
    monitor_interval_sec: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )
    # 期望开播时段（如 {"days":["sat","sun"],"start":"20:00","end":"23:00"}），空=全天值守
    expected_schedule: Mapped[dict | None] = mapped_column(JSONB)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


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
