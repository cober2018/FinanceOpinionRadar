"""监控看板与安全设置 API（Plan #4 后续：前端"监控/安全"页数据面）。"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.services import live_status

router = APIRouter(tags=["monitoring"])

DbDep = Annotated[Session, Depends(get_db)]

logger = structlog.get_logger(__name__)


class SecuritySettingsPayload(BaseModel):
    discover_dispatch_stagger_max_sec: int | None = None
    douyin_discover_max_pages: int | None = None
    proxy_pool: list[str] | None = None


@router.get("/live/monitors")
def get_live_monitors(session: DbDep):
    """监控看板：douyin 账号逐行状态（在播/同步/最近会话/转录）。"""
    return live_status.build_live_monitors(session)


@router.get("/settings/security")
def get_security_settings(session: DbDep):
    return live_status.get_security_settings(session)


@router.put("/settings/security")
def put_security_settings(body: SecuritySettingsPayload, session: DbDep):
    try:
        return live_status.put_security_settings(session, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/system/status")
def get_system_status(session: DbDep):
    """引擎状态（总览页）：ASR 引擎、dtk 可达性、录制器同步路数。只读探测，失败不抛。"""
    import httpx

    s = get_settings()
    reachable = False
    if s.douyin_api_base_url:
        try:
            httpx.get(f"{s.douyin_api_base_url.rstrip('/')}/docs", timeout=2.0)
            reachable = True
        except httpx.HTTPError:
            reachable = False

    synced = 0
    if s.recorder_config_path:
        from pathlib import Path

        p = Path(s.recorder_config_path)
        if p.exists():
            try:
                import json

                synced = sum(1 for r in json.loads(p.read_text()) if isinstance(r, dict))
            except (json.JSONDecodeError, OSError):
                synced = 0

    model = s.asr_mlx_model if s.asr_provider == "mlx" else s.asr_model_name
    return {
        "asr_provider": s.asr_provider,
        "asr_model": model,
        "douyin": {"configured": bool(s.douyin_api_base_url), "reachable": reachable},
        "recorder": {
            "configured": bool(s.recorder_config_path),
            "container": s.recorder_container_name,
            "synced_monitors": synced,
        },
    }


@router.get("/dashboard")
def get_dashboard(
    session: Annotated[Session, Depends(get_db)],
    date: str | None = None,  # noqa: A002 与 PRD 参数名一致
):
    """RAD-071：单请求聚合 Dashboard payload（统计卡/共识/直播间/最近观点）。"""
    from datetime import date as date_cls
    from datetime import timedelta

    from app.db.models import Creator, Topic, TopicConsensusDaily, Viewpoint
    from app.services.extraction import EXTRACTOR_VERSION, PROMPT_VERSION

    day = date_cls.fromisoformat(date) if date else date_cls.today()
    week_ago = day - timedelta(days=7)

    monitors = live_status.build_live_monitors(session)
    live_count = sum(1 for m in monitors if m["is_live"] is True)
    watching = sum(1 for m in monitors if m["live_monitor_enabled"])
    total_segments = sum(m["transcript_count"] for m in monitors)

    recent_vps = (
        session.query(
            Viewpoint.id,
            Viewpoint.claim,
            Viewpoint.stance,
            Viewpoint.confidence,
            Viewpoint.verification_status,
            Viewpoint.as_of_date,
            Creator.display_name.label("creator_name"),
            Topic.canonical_name.label("topic_name"),
        )
        .join(Creator, Creator.id == Viewpoint.creator_id)
        .outerjoin(Topic, Topic.id == Viewpoint.topic_id)
        .order_by(Viewpoint.id.desc())
        .limit(12)
        .all()
    )
    consensus = (
        session.query(
            TopicConsensusDaily,
            Topic.canonical_name.label("topic_name"),
        )
        .join(Topic, Topic.id == TopicConsensusDaily.topic_id)
        .filter(TopicConsensusDaily.trade_date == day)
        .all()
    )
    new_vp_7d = (
        session.query(func.count(Viewpoint.id))
        .filter(Viewpoint.created_at >= week_ago)
        .scalar()
    )
    pending_review = (
        session.query(func.count(Viewpoint.id))
        .filter(Viewpoint.verification_status.in_(["candidate", "needs_review"]))
        .scalar()
    )

    return {
        "date": day.isoformat(),
        "stats": {
            "monitors": len(monitors),
            "live_watching": watching,
            "is_live": live_count,
            "live_segments": total_segments,
            "new_viewpoints_7d": new_vp_7d,
            "pending_review": pending_review,
            "extraction": {
                "prompt_version": PROMPT_VERSION,
                "extractor_version": EXTRACTOR_VERSION,
            },
        },
        "consensus": [
            {
                "topic_id": c.topic_id,
                "topic_name": tname,
                "trade_date": c.trade_date.isoformat(),
                "creator_count": c.creator_count,
                "bullish": c.bullish_count,
                "neutral": c.neutral_count,
                "bearish": c.bearish_count,
                "net_stance_score": (
                    float(c.net_stance_score) if c.net_stance_score is not None else None
                ),
                "disagreement_score": (
                    float(c.disagreement_score) if c.disagreement_score is not None else None
                ),
            }
            for c, tname in consensus
        ],
        "live_rooms": [
            {
                "account_id": m["account_id"],
                "display_name": m["display_name"],
                "room_id": m["room_id"],
                "is_live": m["is_live"],
                "session_status": m["session_status"],
                "segment_count": m["segment_count"],
                "transcript_count": m["transcript_count"],
            }
            for m in monitors
            if m["live_monitor_enabled"]
        ],
        "recent_viewpoints": [
            {
                "id": r.id,
                "claim": r.claim,
                "stance": r.stance,
                "confidence": float(r.confidence or 0.5),
                "status": r.verification_status,
                "creator_name": r.creator_name,
                "topic_name": r.topic_name,
                "as_of_date": r.as_of_date.isoformat() if r.as_of_date else None,
            }
            for r in recent_vps
        ],
    }


@router.get("/jobs")
def list_jobs(
    session: Annotated[Session, Depends(get_db)],
    limit: int = 50,
    job_type: str | None = None,
    status: str | None = None,
):
    """RAD-089 Job Center：任务执行记录（状态/耗时/attempt/trace/错误）。"""
    from app.db.models import JobRun

    q = session.query(JobRun).order_by(JobRun.id.desc())
    if job_type:
        q = q.filter(JobRun.job_type == job_type)
    if status:
        q = q.filter(JobRun.status == status)
    rows = q.limit(min(limit, 200)).all()
    return [
        {
            "id": r.id,
            "job_type": r.job_type,
            "status": r.status,
            "source_item_id": r.source_item_id,
            "attempt": r.attempt,
            "trace_id": r.trace_id,
            "duration_ms": int(
                (r.finished_at - r.started_at).total_seconds() * 1000
            )
            if r.started_at and r.finished_at
            else None,
            "error_code": r.error_code,
            "error_message": (r.error_message or "")[:200] or None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
