"""VideoSummaryEvent 事件存储（delivery 域入口，design 2.1 数据归属表）。

事件行的写入/读取/序号归 delivery；本模块不做审核资格判断（资格/指纹由
insights.review_eligibility 计算，经 workflows 协调后调用这里落事件）。
参与跨域用例时只修改/flush，不自行 commit（design 2.2）。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.runtime.db.models import VideoSummaryEvent


def lock_event_order(session: Session) -> None:
    """事件顺序锁：持有至 commit，防止序号按游标顺序可见（原锁序第二级）。"""
    from sqlalchemy import text

    session.execute(text("SELECT pg_advisory_xact_lock(867530901)"))


def latest_event(session: Session, item_id: int) -> VideoSummaryEvent | None:
    return session.scalars(
        select(VideoSummaryEvent)
        .where(VideoSummaryEvent.item_id == item_id)
        .order_by(VideoSummaryEvent.version.desc())
        .limit(1)
    ).first()


def latest_versions(session: Session, item_ids: Sequence[int]) -> dict[int, int]:
    """多条目最新版本号（valid_summary_candidates 批量资格复核用）。"""
    return dict(session.execute(
        select(VideoSummaryEvent.item_id, func.max(VideoSummaryEvent.version))
        .where(VideoSummaryEvent.item_id.in_(list(item_ids)))
        .group_by(VideoSummaryEvent.item_id)
    ).all())


def record_event(
    session: Session,
    item_id: int,
    version: int,
    state: str,
    fingerprint: str | None,
    payload: dict,
) -> VideoSummaryEvent:
    event = VideoSummaryEvent(
        item_id=item_id, version=version, state=state,
        fingerprint=fingerprint, payload_json=payload,
    )
    session.add(event)
    session.flush()
    return event


def serialize_event(event: VideoSummaryEvent) -> dict:
    return {"event_id": f"vsum-{event.id}", **event.payload_json}


def withdrawn_payload(previous: VideoSummaryEvent) -> dict:
    """撤回事件 payload：继承上一版内容、清空 summary、记录 withdraws_version。"""
    return {
        **previous.payload_json,
        "version": previous.version + 1,
        "state": "withdrawn",
        "summary": None,
        "withdraws_version": previous.version,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def erased_marker_payload(previous: VideoSummaryEvent, item_id: int) -> dict:
    """手动删除后的极简撤回标记（导出内容抹除，仅留版本连续性）。"""
    version = previous.version + 1
    return {
        "item_id": item_id,
        "version": version,
        "state": "withdrawn",
        "summary": None,
        "display_name": None,
        "video_time": None,
        "time_basis": "unknown",
        "withdraws_version": previous.version,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def erase_history(session: Session, item_id: int) -> None:
    """删除该条目全部事件行（手动删除路径；调用方负责先落撤回标记）。"""
    from sqlalchemy import delete

    session.execute(delete(VideoSummaryEvent).where(VideoSummaryEvent.item_id == item_id))
    session.flush()
