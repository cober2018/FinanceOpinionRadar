"""模型定义（统一架构：按数据归属归入拥有者模块）。

原 db/models/*.py 拆分迁入；表名、列、约束完全不变（兼容基线见 evidence/baseline-2）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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

from app.runtime.db.base import Base, IdMixin, TimestampMixin


class ApiKey(TimestampMixin, IdMixin, Base):
    """开放 API 密钥：明文 rk_<32hex> 仅创建时回显一次，库存 sha256。"""

    __tablename__ = "api_key"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(8), nullable=False)  # 明文前 8 位，辨认用
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiCallLog(IdMixin, Base):
    """开放接口调用审计：鉴权失败（key 无效）也记一行，api_key_id 为空。"""

    __tablename__ = "api_call_log"

    api_key_id: Mapped[int | None] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PushChannel(TimestampMixin, IdMixin, Base):
    """推送渠道：飞书/钉钉群机器人或通用 webhook。"""

    __tablename__ = "push_channel"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # generic_webhook / feishu / dingtalk
    config_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    summary_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)


class PushDelivery(TimestampMixin, IdMixin, Base):
    """投递日志：channel × viewpoint 的送达状态（幂等真源 + 重试计数）。"""

    __tablename__ = "push_delivery"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("push_channel.id", ondelete="CASCADE"), nullable=False, index=True
    )
    viewpoint_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class VideoSummaryEvent(TimestampMixin, IdMixin, Base):
    """对外视频结论的不可变 ready/withdrawn 事件。"""

    __tablename__ = "video_summary_event"
    __table_args__ = (
        UniqueConstraint("item_id", "version", name="uq_video_summary_event_item_version"),
    )

    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)


class VideoSummaryDelivery(TimestampMixin, IdMixin, Base):
    """渠道与事件的投递状态，独立于逐观点 PushDelivery。"""

    __tablename__ = "video_summary_delivery"
    __table_args__ = (
        UniqueConstraint("channel_id", "event_id", name="uq_video_summary_delivery_channel_event"),
    )

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("push_channel.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("video_summary_event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)


class AuditLog(IdMixin, Base):
    """所有人工修改必须落此表（PRD 13.15）；write 服务推迟至 EPIC-05（ADR-0006）。"""

    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONB)
    after_json: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

