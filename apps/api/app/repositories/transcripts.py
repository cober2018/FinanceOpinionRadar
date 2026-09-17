"""transcript_segment 仓储（RAD-034）：同一 item 一次只保留一份 transcript。

ENG-4A：TranscriptWrite 字段 confidence/speaker 与模型列 asr_confidence/speaker_label
不同名——显式映射，禁止 **asdict(s)（会传错列名直接 TypeError）。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import TranscriptSegment


@dataclass(frozen=True)
class TranscriptWrite:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    language: str | None = None
    speaker: str | None = None


class TranscriptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_for_item(
        self, source_item_id: int, segments: Sequence[TranscriptWrite]
    ) -> int:
        # 先整体校验再删除写入：坏段不让旧数据被清掉
        for seq, s in enumerate(segments):
            if s.start_ms >= s.end_ms:
                raise ValueError(f"段 {seq} 时间非法: {s.start_ms} >= {s.end_ms}")
        self._session.execute(
            delete(TranscriptSegment).where(
                TranscriptSegment.source_item_id == source_item_id
            )
        )
        for seq, s in enumerate(segments):
            self._session.add(
                TranscriptSegment(
                    source_item_id=source_item_id,
                    sequence_no=seq,
                    start_ms=s.start_ms,
                    end_ms=s.end_ms,
                    text=s.text,
                    asr_confidence=s.confidence,  # ENG-4A：显式列映射
                    language=s.language,
                    speaker_label=s.speaker,  # ENG-4A：显式列映射
                )
            )
        return len(segments)

    def list_for_item(self, source_item_id: int) -> list[TranscriptSegment]:
        return list(
            self._session.query(TranscriptSegment)
            .filter(TranscriptSegment.source_item_id == source_item_id)
            .order_by(TranscriptSegment.sequence_no)
            .all()
        )

    def count_for_item(self, source_item_id: int) -> int:
        return (
            self._session.query(TranscriptSegment)
            .filter(TranscriptSegment.source_item_id == source_item_id)
            .count()
        )
