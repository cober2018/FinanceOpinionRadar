"""模型定义（统一架构：按数据归属归入拥有者模块）。

原 db/models/*.py 拆分迁入；表名、列、约束完全不变（兼容基线见 evidence/baseline-2）。
"""

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.runtime.db.base import Base, IdMixin, TimestampMixin


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
    # 抽取时模型给的原始标的名（如「黄金」「中证500」）：entity 词典未命中时
    # 靠它展示标的，不让观点丢了「对什么」这个维度
    entity_raw: Mapped[str | None] = mapped_column(String(200))
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
    # EPIC-05：裁决来源（rule=规则复核/llm=LLM 复核/manual=人工），谁把它推进到
    # 当前 verification_status 的；LLM 复核留的裁决理由写 review_note
    review_source: Mapped[str | None] = mapped_column(String(20))
    review_note: Mapped[str | None] = mapped_column(Text)


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


class Topic(TimestampMixin, IdMixin, Base):
    __tablename__ = "topic"
    __table_args__ = {"comment": "主题词典：canonical_name 唯一"}

    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    topic_type: Mapped[str] = mapped_column(String(30), nullable=False, default="macro")
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Entity(TimestampMixin, IdMixin, Base):
    __tablename__ = "entity"

    entity_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # stock/index/commodity/...
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    symbol: Mapped[str | None] = mapped_column(String(50))
    market: Mapped[str | None] = mapped_column(String(30))
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class EntityCandidate(TimestampMixin, IdMixin, Base):
    """归一化失败的未知实体：待人工/LLM 消歧后晋升为正式 Entity（RAD-044）。"""

    __tablename__ = "entity_candidate"
    __table_args__ = (
        UniqueConstraint("raw_name", "entity_type", name="uq_entity_candidate_raw_type"),
    )

    raw_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    first_seen_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_item.id", ondelete="SET NULL")
    )


class CreatorTopicSnapshot(Base):
    __tablename__ = "creator_topic_snapshot"
    __table_args__ = {"comment": "每人物每主题每日最新有效观点快照（PRD 8.1）"}

    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creator.id", ondelete="CASCADE"), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topic.id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    latest_viewpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey("viewpoint.id", ondelete="SET NULL")
    )
    stance: Mapped[str | None] = mapped_column(String(30))
    horizon: Mapped[str | None] = mapped_column(String(30))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    change_type: Mapped[str | None] = mapped_column(String(30))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TopicConsensusDaily(Base):
    __tablename__ = "topic_consensus_daily"

    topic_id: Mapped[int] = mapped_column(
        ForeignKey("topic.id", ondelete="CASCADE"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    creator_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bullish_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    neutral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bearish_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bullish_ratio: Mapped[float | None] = mapped_column(Numeric(6, 4))
    net_stance_score: Mapped[float | None] = mapped_column(Numeric(6, 4))
    disagreement_score: Mapped[float | None] = mapped_column(Numeric(6, 4))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ContentSummary(TimestampMixin, IdMixin, Base):
    """结论快照（Plan #6）：item 到期物理删除前，把其观点结论打包留存。

    内容中心生成文章的数据源之一：按 creator_name + item_created_at 时间轴消费。
    不挂 source_item FK（item 已删，快照独立存活）；viewpoints=[] 表示该条目
    处理过但无有效观点。
    """

    __tablename__ = "content_summary"
    __table_args__ = (
        Index("ix_content_summary_creator_time", "creator_name", "item_created_at"),
    )

    platform: Mapped[str] = mapped_column(String(30), nullable=False, default="douyin")
    creator_name: Mapped[str] = mapped_column(String(200), nullable=False)
    item_title: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False, default="vod")
    item_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    item_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewpoints: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 生命周期清理时登记的转录对象 storage_uri（用户语义 2026-09-24：自然过期条目
    # 的转录文本要保留）——孤儿回收任务据此豁免，不误删已清理条目的转录文件
    transcript_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


__all__ = ["ContentSummary"]


class PromptVersion(TimestampMixin, IdMixin, Base):
    __tablename__ = "prompt_version"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_version_name_version"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checksum: Mapped[str | None] = mapped_column(String(64))

