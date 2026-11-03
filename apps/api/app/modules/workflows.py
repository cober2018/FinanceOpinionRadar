"""跨模块用例协调入口（design 2.1/2.2）。

本模块组合各域入口完成跨域用例，持有同一 session 的提交/回滚责任；参与域入口
只修改/flush，不自行 commit（单域用例仍可自持事务）。职责归属（design 2.1 表）：
- 审核/来源资格与指纹：insights.review_eligibility 计算；
- VideoSummaryEvent 事件行：delivery.summary_events 读写；
- SourceItem 总结字段：content.summaries 写入口。

锁序原样保留：先锁 SourceItem 行，再取事件顺序锁（delivery），持有至 commit；
指纹字节序、撤回/ready 原子性与生成后复核语义与原 video_summary_feed 实现一致。

依赖方向：入口（API 路由 / Celery 任务体）→ 本模块 → 各域入口；各域不反向调用
本模块。唯一明文豁免：delivery/open_api 的实时资格组合查询（design 2.1）。
"""

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.delivery.summary_events import (
    erase_history,
    erased_marker_payload,
    latest_event,
    latest_versions,
    lock_event_order,
    record_event,
    withdrawn_payload,
)
from app.modules.insights.review_eligibility import (
    review_state,
    review_state_from_rows,
    source_context,
    video_context,
)
from app.runtime.db.models import (
    Creator,
    Entity,
    SourceAccount,
    SourceItem,
    VideoSummaryEvent,
    Viewpoint,
)
from app.runtime.settings import get_settings

logger = structlog.get_logger(__name__)


def _lock_source_item(session: Session, item_id: int) -> None:
    session.scalar(select(SourceItem).where(SourceItem.id == item_id).with_for_update())


def _lock_item_for_review(session: Session, vp: Viewpoint) -> None:
    _lock_source_item(session, vp.source_item_id)
    session.refresh(vp)


# ---------------------------------------------------------------------------
# 事件协调原语（供本模块用例与开放接口组合查询复用；语义与锁序不变）
# ---------------------------------------------------------------------------


def withdraw_if_invalid(
    session: Session, item_id: int, *, force: bool = False
) -> VideoSummaryEvent | None:
    """任何实质性复核/来源变更后、其事务提交前调用。"""
    _lock_source_item(session, item_id)
    lock_event_order(session)
    previous = latest_event(session, item_id)
    if previous is None or previous.state != "ready":
        return None
    item = session.get(SourceItem, item_id)
    pending, confirmed, fingerprint = review_state(session, item_id) if item else (1, 0, "")
    if not force and item and confirmed and not pending and fingerprint == previous.fingerprint:
        return None
    return record_event(
        session, item_id, previous.version + 1, "withdrawn", None,
        withdrawn_payload(previous),
    )


def record_ready_summary(
    session: Session, item: SourceItem, *, expected_fingerprint: str | None = None
) -> VideoSummaryEvent:
    """Freeze one eligible generated summary in the same transaction as SourceItem."""
    _lock_source_item(session, item.id)
    lock_event_order(session)
    pending, confirmed, fingerprint = review_state(session, item.id)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise ValueError("生成期间观点或视频来源已变化，请重新生成总结")
    if pending or not confirmed or not (item.viewpoint_summary or "").strip():
        raise ValueError("视频总结尚未具备对外交付资格")
    withdraw_if_invalid(session, item.id)
    previous = latest_event(session, item.id)
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    version = (previous.version if previous else 0) + 1
    payload = {
        "item_id": item.id,
        "version": version,
        "state": "ready",
        **video_context(session, item),
        "summary": item.viewpoint_summary,
        "generated_at": (
            item.summary_generated_at.isoformat() if item.summary_generated_at else now.isoformat()
        ),
        "updated_at": now.isoformat(),
    }
    return record_event(session, item.id, version, "ready", fingerprint, payload)


def erase_deleted_item_history(session: Session, item_id: int) -> VideoSummaryEvent | None:
    """Manual deletion erases exported content but leaves a minimal withdrawal marker."""
    _lock_source_item(session, item_id)
    lock_event_order(session)
    previous = latest_event(session, item_id)
    if previous is None:
        return None
    erase_history(session, item_id)
    return record_event(
        session, item_id, previous.version + 1, "withdrawn", None,
        erased_marker_payload(previous, item_id),
    )


def current_event(session: Session, item_id: int) -> VideoSummaryEvent | None:
    event = latest_event(session, item_id)
    if event is None or event.state != "ready":
        return None
    item = session.get(SourceItem, item_id)
    if item is None:
        return None
    pending, confirmed, fingerprint = review_state(session, item_id)
    return event if confirmed and not pending and fingerprint == event.fingerprint else None


def valid_summary_candidates(
    session: Session, candidates: list[tuple[SourceItem, VideoSummaryEvent]]
) -> set[int]:
    """开放接口实时资格组合查询（design 2.1 明文允许的调用方向）。"""
    if not candidates:
        return set()
    item_ids = [item.id for item, _ in candidates]
    latest = latest_versions(session, item_ids)
    reviews: dict[int, list[tuple[Viewpoint, str | None]]] = {item_id: [] for item_id in item_ids}
    for viewpoint, name in session.execute(
        select(Viewpoint, Entity.canonical_name)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.source_item_id.in_(item_ids)).order_by(Viewpoint.id)
    ):
        reviews[viewpoint.source_item_id].append((viewpoint, name))
    accounts = {
        account_id: (name, platform)
        for account_id, name, platform in session.execute(
            select(SourceAccount.id, Creator.display_name, SourceAccount.platform)
            .join(Creator, Creator.id == SourceAccount.creator_id)
            .where(SourceAccount.id.in_({item.source_account_id for item, _ in candidates}))
        )
    }
    valid = set()
    for item, event in candidates:
        name, platform = accounts.get(item.source_account_id, (None, None))
        pending, confirmed, fingerprint = review_state_from_rows(
            reviews[item.id], source_context(item, name, platform),
        )
        if (
            event.state == "ready" and latest.get(item.id) == event.version
            and confirmed and not pending and fingerprint == event.fingerprint
        ):
            valid.add(item.id)
    return valid


# ---------------------------------------------------------------------------
# 跨域用例（workflows 持有 session 与提交责任）
# ---------------------------------------------------------------------------


def summarize_item_viewpoints(session: Session, item_id: int, *, provider=None) -> dict:
    """confirmed 观点 → LLM 一句话总结 → content 总结字段 + ready 事件同事务。

    原 insights.summarizer.summarize_source_item 全流程迁移：门禁与 LLM 输入组装经
    insights 入口（模型调用在写事务之外）；写回经 content.summaries 与本模块事件
    协调；生成后指纹复核与回滚语义原样保留。
    """
    from app.modules.content.summaries import save_viewpoint_summary
    from app.modules.insights import summarizer
    from app.modules.insights.llm_config import build_llm_provider

    item = session.get(SourceItem, item_id)
    if item is None:
        return {"item_id": item_id, "status": "skipped_no_item"}

    cand = summarizer.gate_and_build(session, item)
    if cand["status"] != "ok":
        if cand["status"] == "skipped_not_all_confirmed":
            logger.info(
                "summarize_gate_blocked", item_id=item_id, non_confirmed=cand["non_confirmed"]
            )
            return {
                "item_id": item_id,
                "status": "skipped_not_all_confirmed",
                "non_confirmed": cand["non_confirmed"],
            }
        return {"item_id": item_id, "status": cand["status"]}

    provider = provider or build_llm_provider(session, task="summary")
    resp = provider.generate_json(cand["system"], cand["user"], cand["schema"], temperature=0.2)
    data = resp.data if isinstance(resp.data, dict) else {}
    summary = str(data.get("summary") or "").strip()
    if not summary:
        # 键名漂移兜底：唯一字符串值直接采纳（MiniMax 对 schema 键名有自由发挥）
        strings = [v for v in data.values() if isinstance(v, str) and v.strip()]
        summary = strings[0].strip() if len(strings) == 1 else ""

    if not summary:
        logger.warning(
            "summarize_empty_output",
            item_id=item_id,
            raw_head=getattr(resp, "raw_head", "")[:120],
        )
        return {"item_id": item_id, "status": "skipped_empty_output"}

    # 写阶段：重新锁定 + 指纹复核（生成期间观点/来源可能已变化）
    _lock_source_item(session, item_id)
    session.expire_all()
    item = session.get(SourceItem, item_id)
    if item is None:
        session.rollback()
        return {"item_id": item_id, "status": "skipped_no_item"}
    pending, confirmed, current_fingerprint = review_state(session, item_id)
    if pending or not confirmed or current_fingerprint != cand["fingerprint"]:
        session.rollback()
        return {"item_id": item_id, "status": "skipped_stale_input"}
    save_viewpoint_summary(session, item, summary)
    record_ready_summary(session, item, expected_fingerprint=cand["fingerprint"])
    session.commit()
    logger.info("summarize_done", item_id=item_id, chars=len(summary))
    return {"item_id": item_id, "status": "done", "summary": summary}


def confirm_viewpoint(session: Session, vp: Viewpoint, *, reason: str | None = None) -> dict:
    """人工确认 → confirmed + 快照构建（RAD-060）+ item ready 收尾检查。"""
    from app.modules.insights.audit import write_audit
    from app.modules.insights.history import build_snapshot
    from app.modules.insights.summarizer import promote_item_if_reviewed

    _lock_item_for_review(session, vp)
    before = {"verification_status": vp.verification_status}
    if vp.verification_status == "confirmed":
        return {"id": vp.id, "verification_status": "confirmed", "already": True}
    vp.verification_status = "confirmed"
    vp.review_source = "manual"
    withdraw_if_invalid(session, vp.source_item_id)
    session.commit()
    write_audit(
        session,
        actor="console",
        action="confirm",
        resource_type="viewpoint",
        resource_id=vp.id,
        before=before,
        after={"verification_status": "confirmed"},
        reason=reason,
    )
    if vp.topic_id is not None:
        build_snapshot(session, vp)
    item = session.get(SourceItem, vp.source_item_id)
    if item is not None:
        promote_item_if_reviewed(session, item)
    session.commit()
    return {"id": vp.id, "verification_status": "confirmed"}


def reject_viewpoint(session: Session, vp: Viewpoint, *, reason: str | None = None) -> dict:
    """人工驳回 → rejected（不删数据）。"""
    from app.modules.insights.audit import write_audit
    from app.modules.insights.summarizer import promote_item_if_reviewed

    _lock_item_for_review(session, vp)
    before = {"verification_status": vp.verification_status}
    vp.verification_status = "rejected"
    vp.review_source = "manual"
    withdraw_if_invalid(session, vp.source_item_id)
    session.commit()
    write_audit(
        session,
        actor="console",
        action="reject",
        resource_type="viewpoint",
        resource_id=vp.id,
        before=before,
        after={"verification_status": "rejected"},
        reason=reason,
    )
    item = session.get(SourceItem, vp.source_item_id)
    if item is not None:
        promote_item_if_reviewed(session, item)
    session.commit()
    return {"id": vp.id, "verification_status": "rejected"}


def apply_viewpoint_patch(
    session: Session, vp: Viewpoint, updates: dict, *, reason: str | None = None
) -> dict:
    """人工修正写回 + 资格撤回 + 审计（RAD-052；入口校验/实体归一在 API 层完成）。"""
    from app.modules.insights.audit import write_audit

    _lock_item_for_review(session, vp)
    before = {k: getattr(vp, k) for k in updates}
    for k, v in updates.items():
        setattr(vp, k, v)
    withdraw_if_invalid(session, vp.source_item_id)
    session.commit()
    write_audit(
        session,
        actor="console",
        action="patch",
        resource_type="viewpoint",
        resource_id=vp.id,
        before={k: (str(v) if v is not None else None) for k, v in before.items()},
        after={k: (str(v) if v is not None else None) for k, v in updates.items()},
        reason=reason,
    )
    session.commit()  # 审计单独落库：write_audit 只 add 不 commit，漏提交会被会话关闭回滚
    return {"id": vp.id, "updated": sorted(updates.keys())}


def reextract_item_viewpoints(session: Session, item: SourceItem) -> bool:
    """删旧观点（级联证据）→ 状态回 extracting → 派发抽取任务。返回是否派发。"""
    from app.bootstrap.celery import celery_app
    from app.runtime.pipeline import ensure_transition

    if item.status not in ("transcribed", "extracting", "reviewing", "ready"):
        return False
    _lock_source_item(session, item.id)
    session.query(Viewpoint).filter(Viewpoint.source_item_id == item.id).delete(
        synchronize_session=False
    )
    withdraw_if_invalid(session, item.id, force=True)
    if item.status != "extracting":
        ensure_transition(item.status, "extracting")
        item.status = "extracting"
    session.commit()
    celery_app.send_task("extract_source_item_viewpoints", args=[item.id])
    return True


def delete_source_item(session: Session, item: SourceItem) -> dict:
    """删除单条内容（物理级联 + 墓碑 + 事件历史抹除）。"""
    from app.modules.content.deletion import purge_item_files, tombstone_if_absent

    erase_deleted_item_history(session, item.id)
    tombstone_if_absent(session, item.source_account_id, item.external_item_id)
    purge_item_files(session, item)
    session.delete(item)
    session.commit()
    return {"deleted": item.id, "tombstoned": True}


def batch_delete_source_items(session: Session, ids: list[int]) -> dict:
    """批量物理删除（视频库多选）：逐条级联 + 墓碑，返回成功/失败清单。"""
    from app.modules.content.deletion import purge_item_files, tombstone_if_absent

    deleted: list[int] = []
    missing: list[int] = []
    for item_id in ids:
        item = session.get(SourceItem, item_id)
        if item is None:
            missing.append(item_id)
            continue
        erase_deleted_item_history(session, item.id)
        tombstone_if_absent(session, item.source_account_id, item.external_item_id)
        purge_item_files(session, item)
        session.delete(item)
        deleted.append(item_id)
    session.commit()
    return {"deleted": deleted, "missing": missing}


def sweep_expired_content(session, *, dry_run: bool = False, settings=None) -> dict:
    """内容生命周期清理（原 content.retention.sweep_expired_content 全流程迁移）。

    到期选择与逐条过期（快照/墓碑/弹幕档案/删行）经 content 入口；强制撤回在
    同一事务内先于删行完成；单飞闸、计数口径与日志事件名原样保留。
    """
    from app.modules.content import retention

    s = settings or get_settings()
    zero = {"expired": 0, "swept": 0, "snapshots": 0, "tombstones": 0, "sink_files": 0}
    retention_days = s.content_retention_days
    if not retention_days or retention_days <= 0:
        retention.logger.info("retention_sweep_noop", reason="content_retention_days=0（禁用）")
        return {**zero, "noop": "content_retention_days=0"}
    locked = session.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": retention.SWEEP_LOCK_KEY}
    ).scalar()
    session.commit()
    if not locked:
        retention.logger.info("retention_sweep_skipped_singleton")
        return {**zero, "skipped": "singleton"}
    try:
        items, creator_names = retention.select_expired(session, retention_days=retention_days)
        counters = {**zero, "expired": len(items)}
        if dry_run:
            retention.logger.info("retention_sweep_dry_run", **counters)
            return counters
        for item in items:
            withdraw_if_invalid(session, item.id, force=True)
            deltas = retention.expire_item(
                session,
                item,
                creator_name=creator_names.get(item.source_account_id),
                sink_dir=s.danmaku_sink_dir,
            )
            for k, v in deltas.items():
                counters[k] += v
            counters["swept"] += 1
        session.commit()
        retention.logger.info("retention_sweep_done", retention_days=retention_days, **counters)
        return counters
    finally:
        session.rollback()
        session.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": retention.SWEEP_LOCK_KEY}
        )
        session.commit()


def upsert_source_account(
    session: Session,
    *,
    platform: str,
    url: str,
    display_name: str | None,
    discovery_mode: str,
    poll_interval_sec: int,
    poll_interval_min_sec: int | None,
    poll_interval_max_sec: int | None,
    live_room_url: str | None,
    config_json: dict,
):
    """注册/复活来源账号（原 sources.api.create_source_account 流程迁移）。

    sources 域 upsert/改名与复活改名引发的 delivery 撤回同事务提交（design 2.2）；
    身份查重按稳定频道身份跨 URL 形态判定。入口层 URL/直播间形态校验已先行。
    """
    from app.modules.collection.discovery import FALLBACK_CREATOR_NAME, get_or_create_creator
    from app.modules.sources.identity import (
        channel_identity,
        derive_external_id,
        reject_duplicate_channel,
        resolve_youtube_channel_id,
    )
    from app.modules.sources.repository import SourceAccountRepository

    external_id = derive_external_id(platform, url)
    # 注册查重：同一频道多形态 URL（channel_id / @handle / space mid）视为同一身份
    identity = channel_identity(platform, url)
    # @handle 与 channel_id 字符串不相交——跨形态需 resolve 出 channel_id 才能识别同频道
    if platform == "youtube" and identity and identity.startswith("@"):
        identity = resolve_youtube_channel_id(url) or identity
    if identity:
        reject_duplicate_channel(session, platform, identity)

    creator = get_or_create_creator(session, display_name)
    account = SourceAccountRepository(session).upsert_by_external(
        creator_id=creator.id,
        platform=platform,
        external_id=external_id,
        url=url,
        discovery_mode=discovery_mode,
    )
    # upsert 撞上历史/复活账号（creator_id 不可变）时：占位名升级为用户填的真名
    if display_name:
        creator = session.get(Creator, account.creator_id)
        if creator is not None and creator.display_name in (
            external_id,
            FALLBACK_CREATOR_NAME,
        ):
            creator.display_name = display_name
            item_ids = session.scalars(
                select(SourceItem.id)
                .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
                .where(SourceAccount.creator_id == creator.id)
                .order_by(SourceItem.id)
                .with_for_update(of=SourceItem)
            ).all()
            for item_id in item_ids:
                withdraw_if_invalid(session, item_id)
    account.poll_interval_sec = poll_interval_sec
    account.poll_interval_min_sec = poll_interval_min_sec
    account.poll_interval_max_sec = poll_interval_max_sec
    if live_room_url is not None:
        account.live_room_url = live_room_url
    account.config_json = config_json
    session.commit()
    return account
