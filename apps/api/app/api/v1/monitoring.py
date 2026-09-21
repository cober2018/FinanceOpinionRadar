"""监控看板与安全设置 API（Plan #4 后续：前端"监控/安全"页数据面）。"""

import json
from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.session import get_db
from app.services import live_status, llm_config, runtime_control

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


class RuntimeSettingsPayload(BaseModel):
    # StrictBool：拒绝 "yes"/1 等宽松强转，开关必须是显式布尔
    autostart: StrictBool | None = None
    watchdog: StrictBool | None = None


@router.get("/settings/runtime")
def get_runtime_settings(session: DbDep):
    """系统服务开关：DB 意图 + launchd 实际加载态（installed，None=launchctl 不可用）。"""
    return runtime_control.get_runtime_settings(session)


@router.put("/settings/runtime")
def put_runtime_settings(body: RuntimeSettingsPayload, session: DbDep):
    try:
        return runtime_control.put_runtime_settings(session, body.model_dump())
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

    from app.db.models import Creator, SourceItem, Topic, TopicConsensusDaily, Viewpoint
    from app.services import llm_config
    from app.services.extraction import EXTRACTOR_VERSION, PROMPT_VERSION

    llm_cfg = llm_config.get_llm_settings(session)
    llm_configured = llm_cfg["effective"]["key_set"] and bool(llm_cfg["effective"]["base_url"])

    day = date_cls.fromisoformat(date) if date else date_cls.today()
    week_ago = day - timedelta(days=7)

    monitors = live_status.build_live_monitors(session)
    live_count = sum(1 for m in monitors if m["is_live"] is True)
    watching = sum(1 for m in monitors if m["live_monitor_enabled"])
    # 统计直查 DB（口径明确）：直播段落 = live 条目下全部 transcript；已转写内容 = 有段落条目
    from app.db.models import TranscriptSegment

    total_segments = (
        session.query(func.count(TranscriptSegment.id))
        .join(SourceItem, SourceItem.id == TranscriptSegment.source_item_id)
        .filter(SourceItem.item_type == "live")
        .scalar()
    )
    transcribed_contents = (
        session.query(func.count(func.distinct(TranscriptSegment.source_item_id))).scalar()
    )

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
            "transcribed_contents": transcribed_contents,
            "new_viewpoints_7d": new_vp_7d,
            "pending_review": pending_review,
            "llm_configured": llm_configured,
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


@router.get("/jobs/stats")
def get_job_stats(session: DbDep) -> dict:
    from app.db.models import JobRun

    rows = session.query(JobRun.status, func.count()).group_by(JobRun.status).all()
    by_status = {s: n for s, n in rows}
    by_type: dict[str, int] = {
        jt: n
        for jt, n in session.query(JobRun.job_type, func.count())
        .group_by(JobRun.job_type)
        .all()
    }
    return {
        "total": sum(by_status.values()),
        "running": by_status.get("running", 0),
        "success": by_status.get("success", 0),
        "failed": by_status.get("failed", 0),
        "by_type": by_type,
    }


@router.get("/jobs")
def list_jobs(
    session: Annotated[Session, Depends(get_db)],
    limit: int = 50,
    job_type: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """RAD-089 Job Center：任务执行记录（状态/耗时/attempt/错误）+ 日期筛选。"""
    from datetime import datetime as dt

    from app.db.models import JobRun

    q = session.query(JobRun).order_by(JobRun.id.desc())
    if job_type:
        q = q.filter(JobRun.job_type == job_type)
    if status:
        q = q.filter(JobRun.status == status)
    if date_from:
        q = q.filter(JobRun.created_at >= dt.fromisoformat(date_from))
    if date_to:
        q = q.filter(JobRun.created_at < dt.fromisoformat(date_to) + timedelta(days=1))
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


class LLMSettingsPayload(BaseModel):
    template: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    chat_path: str | None = None


@router.get("/settings/llm")
def get_llm_settings(session: Annotated[Session, Depends(get_db)]):
    """RAD-041+：大模型配置读取（模板列表 + 当前值 + 生效值；key 只回是否已设置）。"""
    return llm_config.get_llm_settings(session)


@router.put("/settings/llm")
def put_llm_settings(body: LLMSettingsPayload, session: Annotated[Session, Depends(get_db)]):
    try:
        return llm_config.put_llm_settings(session, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/settings/llm/test")
def test_llm_settings(session: Annotated[Session, Depends(get_db)]):
    """连接测试：按当前配置跑一次最小 generate_json，返回时延与样例。"""
    import time

    cfg = llm_config.get_llm_settings(session)
    provider = llm_config.build_llm_provider(session)
    if not cfg["effective"]["key_set"] or not cfg["effective"]["base_url"]:
        return {"ok": False, "error": "未配置完整（base_url/api_key 缺失），当前为 Mock 模式"}
    t0 = time.time()
    try:
        resp = provider.generate_json(
            "你是连通性测试助手。",
            "回复 JSON：{\"ok\": true}",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            temperature=0.0,
        )
        return {
            "ok": True,
            "latency_ms": int((time.time() - t0) * 1000),
            "model": resp.model,
            "sample": json.dumps(resp.data, ensure_ascii=False)[:120],
        }
    except Exception as exc:  # noqa: BLE001 测试端点把上游错误原样带回
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "error": str(exc)[:300]}


class PolishRequest(BaseModel):
    claim: str


@router.post("/llm/polish")
def polish_claim(body: PolishRequest, session: Annotated[Session, Depends(get_db)]):
    """观点润色（人工修改界面用）：固定默认提示词 + app_setting 可覆盖。

    只返回润色文本，不落库——由前端确认后随 PATCH /viewpoints/{id} 提交。
    """

    claim = body.claim.strip()
    if not claim:
        raise HTTPException(status_code=422, detail="内容为空，无法润色")
    provider = llm_config.build_llm_provider(session)
    if type(provider).__name__ == "MockLLMProvider":
        raise HTTPException(status_code=409, detail="大模型未配置，润色不可用（设置 → 大模型）")

    from app.db.models import AppSetting

    row = session.get(AppSetting, "polish_prompt")
    prompt = (row.value or {}).get("template") if row else None
    prompt = prompt or (
        "你是资深财经编辑。请在严格保持原意、立场方向、程度与条件不变的前提下，"
        "把下面的观点陈述润色为一句通顺、专业、简洁的财经观点（不超过 80 字），"
        "修正口语化和错别字。只输出润色后的句子本身，不要任何解释或引号。"
    )

    try:
        resp = provider.generate_json(
            "只输出 JSON。",
            f'{prompt}\n\n原句：{claim}\n\n输出 JSON：{{"polished": "..."}}',
            {"type": "object", "properties": {"polished": {"type": "string"}}},
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 上游错误原样返回给前端
        return {"ok": False, "error": str(exc)[:300]}

    polished = ""
    data = resp.data if isinstance(resp.data, dict) else {}
    for k in ("polished", "result", "text", "content"):
        if isinstance(data.get(k), str) and data[k].strip():
            polished = data[k].strip()
            break
    return {"ok": bool(polished), "polished": polished, "model": resp.model}


@router.get("/ledger")
def get_content_ledger(
    session: Annotated[Session, Depends(get_db)],
    creator_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
):
    """内容台账（任务页主视图）：每条内容一行，持续追踪全生命周期——

    发现 → 转写 → 观点抽取 → 人工复核 → 归档，附各阶段产物计数与错误。
    """
    from app.db.models import (
        Creator,
        SourceAccount,
        SourceItem,
        TranscriptSegment,
        Viewpoint,
    )

    q = (
        session.query(
            SourceItem,
            SourceAccount.platform,
            Creator.display_name,
        )
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .join(Creator, Creator.id == SourceAccount.creator_id)
        .filter(SourceItem.item_type.in_(["vod", "live"]))
        .order_by(SourceItem.id.desc())
    )
    if creator_id:
        q = q.filter(SourceItem.source_account_id == creator_id)
    if status:
        q = q.filter(SourceItem.status == status)
    rows = q.limit(min(limit, 300)).all()

    item_ids = [it.id for it, _, _ in rows] or [0]
    seg_counts: dict[int, int] = {
        si: n
        for si, n in session.query(
            TranscriptSegment.source_item_id, func.count()
        )
        .filter(TranscriptSegment.source_item_id.in_(item_ids))
        .group_by(TranscriptSegment.source_item_id)
        .all()
    }
    vp_rows = (
        session.query(
            Viewpoint.source_item_id,
            Viewpoint.verification_status,
            func.count(),
        )
        .filter(Viewpoint.source_item_id.in_(item_ids))
        .group_by(Viewpoint.source_item_id, Viewpoint.verification_status)
        .all()
    )
    vp_counts: dict[int, dict[str, int]] = {}
    vp_topic: dict[int, str] = {}
    for si, vs, n in vp_rows:
        vp_counts.setdefault(si, {})[vs] = n
    from app.db.models import Topic as TopicModel
    from app.db.models import Viewpoint as VPModel

    first_vp_topics = (
        session.query(VPModel.source_item_id, TopicModel.canonical_name)
        .join(TopicModel, TopicModel.id == VPModel.topic_id)
        .filter(VPModel.source_item_id.in_(item_ids))
        .order_by(VPModel.source_item_id)
        .all()
    )
    for si, tname in first_vp_topics:
        vp_topic.setdefault(si, tname)

    ledger = []
    for item, platform, cname in rows:
        err = (item.metadata_json or {}).get("last_error") or {}
        prog = (item.metadata_json or {}).get("progress") or {}
        vc = vp_counts.get(item.id, {})
        total_vp = sum(vc.values())
        ledger.append(
            {
                "item_id": item.id,
                "creator_name": cname,
                "platform": platform,
                "title": (item.title or "")[:60],
                "item_type": item.item_type,
                "status": item.status,
                "progress_phase": prog.get("phase"),
                "progress_detail": prog.get("detail"),
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "transcript_count": seg_counts.get(item.id, 0),
                "viewpoint_total": total_vp,
                "viewpoint_needs_review": vc.get("needs_review", 0) + vc.get("candidate", 0),
                "viewpoint_confirmed": vc.get("confirmed", 0),
                "viewpoint_rejected": vc.get("rejected", 0),
                "topic_name": vp_topic.get(item.id),
                "error_code": err.get("code"),
                "error_message": (err.get("message") or "")[:160] or None,
            }
        )
    return ledger
