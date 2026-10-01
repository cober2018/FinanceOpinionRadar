"""观点时刻语义（2026-09-30，时间轴与推送共用）：

- vod 观点：发布时刻即内容时刻
- live 观点：起播时刻 + 首条证据在直播内的全局偏移（主播真正说出这句话的钟点）
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import SourceItem, TranscriptSegment, Viewpoint, ViewpointEvidence


def evidence_offset_map(session: Session, vp_ids: list[int]) -> dict[int, int]:
    """批量取观点的证据偏移（证据段最小 start_ms，=直播内全局偏移）。"""
    if not vp_ids:
        return {}
    rows = session.execute(
        select(
            ViewpointEvidence.viewpoint_id,
            func.min(TranscriptSegment.start_ms),
        )
        .join(
            TranscriptSegment,
            TranscriptSegment.id == ViewpointEvidence.transcript_segment_id,
        )
        .where(ViewpointEvidence.viewpoint_id.in_(vp_ids))
        .group_by(ViewpointEvidence.viewpoint_id)
    ).all()
    return {vid: int(offset or 0) for vid, offset in rows}


def said_at_map(session: Session, viewpoints: list[Viewpoint]) -> dict[int, datetime | None]:
    """观点 → 内容时刻。live 用起播+证据偏移；vod/其他用发布时刻；无发布时刻回落 None。

    不依赖 ORM relationship（Viewpoint 未定义 source_item 关系），按 id 批量取条目。
    """
    items = {
        item.id: item
        for item in session.scalars(
            select(SourceItem).where(
                SourceItem.id.in_({vp.source_item_id for vp in viewpoints})
            )
        )
    }
    live_vp_ids = [
        vp.id
        for vp in viewpoints
        if (it := items.get(vp.source_item_id)) is not None
        and it.item_type == "live"
        and it.published_at is not None
    ]
    offsets = evidence_offset_map(session, live_vp_ids)
    out: dict[int, datetime | None] = {}
    for vp in viewpoints:
        item = items.get(vp.source_item_id)
        if item is None or item.published_at is None:
            out[vp.id] = None
            continue
        if item.item_type == "live":
            out[vp.id] = item.published_at + timedelta(milliseconds=offsets.get(vp.id, 0))
        else:
            out[vp.id] = item.published_at
    return out
