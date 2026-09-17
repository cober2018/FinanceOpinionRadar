"""转录全文搜索（视频库搜索框）：ILIKE 扫 transcript_segment，聚合到条目级。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Creator, SourceAccount, SourceItem, TranscriptSegment
from app.db.session import get_db

router = APIRouter(prefix="/transcripts", tags=["transcripts"])

def _search_impl(session: Session, q: str, limit: int = 30):
    """ILIKE 命中计数 + 首条命中上下文摘录（前后各 18 字）。"""
    like = f"%{q}%"
    rows = (
        session.query(
            SourceItem.id,
            SourceItem.title,
            Creator.display_name,
            SourceAccount.platform,
            SourceItem.published_at,
            func.count(TranscriptSegment.id).label("match_count"),
            func.min(TranscriptSegment.text).label("first_text"),
        )
        .join(TranscriptSegment, TranscriptSegment.source_item_id == SourceItem.id)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .filter(TranscriptSegment.text.ilike(like))
        .group_by(SourceItem.id, Creator.display_name, SourceAccount.platform)
        .order_by(func.count(TranscriptSegment.id).desc())
        .limit(limit)
        .all()
    )
    hits = []
    for item_id, title, name, platform, published, count, _first in rows:
        snippet_row = (
            session.query(TranscriptSegment.text)
            .filter(TranscriptSegment.source_item_id == item_id, TranscriptSegment.text.ilike(like))
            .order_by(TranscriptSegment.sequence_no)
            .first()
        )
        snippet = ""
        if snippet_row:
            text = snippet_row[0]
            pos = text.lower().find(q.lower())
            lo, hi = max(0, pos - 18), pos + len(q) + 18
            snippet = ("…" if lo > 0 else "") + text[lo:hi] + ("…" if hi < len(text) else "")
        hits.append(
            {
                "item_id": item_id,
                "title": title,
                "display_name": name,
                "platform": platform,
                "published_at": published,
                "match_count": count,
                "snippet": snippet,
            }
        )
    return hits


@router.get("/search")
def search_transcripts(q: str, session: Annotated[Session, Depends(get_db)]):
    if not q.strip():
        raise HTTPException(status_code=422, detail="搜索词不能为空")
    return _search_impl(session, q.strip())
