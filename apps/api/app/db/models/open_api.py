"""开放数据层模型（Plan #7）：API Key、调用审计、推送渠道与投递日志。

本系统定位产品矩阵的数据底座：/open/v1 只读接口（X-API-Key 鉴权）向兄弟产品
输出粗清洗成果（转录 + 已确认观点）；推送把新确认观点投递到飞书/钉钉/通用 webhook。

JSONB 形状（docstring-only）：
- push_channel.config_json: {"url": str, "secret": str | None}（secret 用于机器人加签）。
- push_delivery.viewpoint_id 不设外键：观点被保留期清理物理删除后，投递日志仍需留存。
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

from app.db.base import Base, IdMixin, TimestampMixin


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
