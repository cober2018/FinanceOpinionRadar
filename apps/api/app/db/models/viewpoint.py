"""观点模型：本系统的事实核心。

时间线语义（D9）：as_of_date = 观点时序锚点（取来源发布日），
人物-主题时间线按 (creator_id, topic_id, as_of_date DESC NULLS LAST, created_at DESC) 排序。
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Viewpoint(TimestampMixin, IdMixin, Base):
    __tablename__ = "viewpoint"
    __table_args__ = (
        Index("ix_viewpoint_creator_topic", "creator_id", "topic_id"),
        Index("ix_viewpoint_creator_topic_asof", "creator_id", "topic_id", "as_of_date"),
        Index("ix_viewpoint_entity", "entity_id"),
        Index("ix_viewpoint_source_item", "source_item_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("importance >= 0 AND importance <= 1", name="importance_range"),
    )

    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creator.id", ondelete="CASCADE"), nullable=False
    )
    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topic.id", ondelete="SET NULL"))
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entity.id", ondelete="SET NULL"))
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    stance: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon: Mapped[str | None] = mapped_column(String(30))  # 未提时间维度则空（合法）
    conditional: Mapped[bool] = mapped_column(nullable=False, default=False)
    importance: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.5)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    change_type: Mapped[str | None] = mapped_column(String(30))
    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="candidate"
    )
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    # RAD-045 去重：该观点吸收了哪些重复候选（"同实体同立场 claim 相似度 0.91"）
    merge_reason: Mapped[str | None] = mapped_column(String(200))


class ViewpointEvidence(IdMixin, Base):
    """证据绑定：观点 ↔ 转录时间区间，Evidence-First（PRD §4.1）。"""

    __tablename__ = "viewpoint_evidence"
    __table_args__ = (
        UniqueConstraint("viewpoint_id", "evidence_order", name="uq_evidence_viewpoint_order"),
        Index("ix_evidence_viewpoint", "viewpoint_id"),
        Index("ix_evidence_segment", "transcript_segment_id"),
    )

    viewpoint_id: Mapped[int] = mapped_column(
        ForeignKey("viewpoint.id", ondelete="CASCADE"), nullable=False
    )
    transcript_segment_id: Mapped[int] = mapped_column(
        ForeignKey("transcript_segment.id", ondelete="CASCADE"), nullable=False
    )
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_order: Mapped[int] = mapped_column(nullable=False, default=1)
