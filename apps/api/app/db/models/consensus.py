"""快照与共识模型（PRD §8.1）。

- creator_topic_snapshot: 每人物每主题每日最新有效观点快照，是时间线与共识的中间层。
- topic_consensus_daily: 每主题每交易日共识指标。
- computed_at（D54）: 计算时刻，与 snapshot_date/trade_date（业务日期）区分，支持重算审计。
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


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
