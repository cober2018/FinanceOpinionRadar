"""内容生命周期 API（Plan #6）：手动清理（预览/执行）与结论快照列表。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/retention", tags=["retention"])

DbDep = Annotated[Session, Depends(get_db)]


@router.post("/sweep")
def run_sweep(session: DbDep, dry_run: bool = False):
    """手动触发一轮清理。dry_run=true 只统计到期条目，不删除（预览）。"""
    from app.services.retention import sweep_expired_content

    return sweep_expired_content(session, dry_run=dry_run)


@router.get("/summaries")
def list_summaries(session: DbDep, limit: int = 100, offset: int = 0):
    """结论快照列表（内容中心数据源预览）：creator + 时间轴倒序。"""
    from app.db.models import ContentSummary

    rows = (
        session.execute(
            select(ContentSummary)
            .order_by(ContentSummary.item_created_at.desc(), ContentSummary.id.desc())
            .limit(min(limit, 500))
            .offset(max(offset, 0))
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "platform": r.platform,
            "creator_name": r.creator_name,
            "item_title": r.item_title,
            "item_type": r.item_type,
            "item_created_at": r.item_created_at,
            "item_published_at": r.item_published_at,
            "viewpoints": r.viewpoints,
        }
        for r in rows
    ]
