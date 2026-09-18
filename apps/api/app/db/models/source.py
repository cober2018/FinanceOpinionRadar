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
    func,
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
    # 值守直播间 URL（live.douyin.com/<room_id>），与发现用主页 url 分离——
    # 一个博主可同时配短视频发现 + 直播值守（解 Plan #4 挂账的 URL 身份冲突）
    live_room_url: Mapped[str | None] = mapped_column(Text)
    discovery_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    poll_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    # 人类化随机轮询（RAD-023 扩展）：设置 [min, max] 后每次发现成功在区间内均匀重抽
    # poll_interval_sec，使下次到点随机化（模仿人工浏览节奏）；为空保持固定间隔，兼容旧账号
    poll_interval_min_sec: Mapped[int | None] = mapped_column(Integer)
    poll_interval_max_sec: Mapped[int | None] = mapped_column(Integer)
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
