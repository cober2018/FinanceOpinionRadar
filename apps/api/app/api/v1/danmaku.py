"""观众互动数据 API（Plan #5 后续，2026-09-19）：弹幕/（将来）视频评论的统计。

产品裁决：前端只展示统计数字 + 详情查看，LLM 分析后置（EPIC-04+）。
弹幕详情在 source_items 路由（/source-items/{id}/chat-messages，与 /transcript 并列）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/danmaku", tags=["danmaku"])

DbDep = Annotated[Session, Depends(get_db)]


@router.get("/stats")
def get_danmaku_stats(session: DbDep):
    """总览统计卡：弹幕累计抓取条数 + 覆盖会话数 + 最近一条时间。"""
    from app.db.models import LiveChatMessage

    total = session.query(func.count(LiveChatMessage.id)).scalar()
    sessions = (
        session.query(func.count(func.distinct(LiveChatMessage.source_item_id))).scalar()
    )
    latest = session.query(func.max(LiveChatMessage.published_at)).scalar()
    return {"total": total, "sessions": sessions, "latest_message_at": latest}
