"""媒体与转录模型。

JSONB 形状（docstring-only，正式 schema 推迟 EPIC-04，见 M4）：
- transcript_segment.metadata_json: ASR 侧附加信息（词级时间戳、语言置信度细分等）。
"""

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class MediaAsset(TimestampMixin, IdMixin, Base):
    __tablename__ = "media_asset"
    __table_args__ = (Index("ix_media_asset_source_item", "source_item_id"),)

    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False)  # video/audio/subtitle
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)


class TranscriptSegment(IdMixin, Base):
    __tablename__ = "transcript_segment"
    __table_args__ = (Index("ix_transcript_segment_item_seq", "source_item_id", "sequence_no"),)

    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False
    )
    speaker_label: Mapped[str | None] = mapped_column(String(100))
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(20))
    asr_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
