"""Reviewed, video-level summary events shared by pull and push."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import Creator, Entity, SourceAccount, SourceItem, VideoSummaryEvent, Viewpoint


def _lock_event_order(session: Session) -> None:
    # Hold until commit so sequence IDs cannot become visible out of cursor order.
    session.execute(text("SELECT pg_advisory_xact_lock(867530901)"))


def _lock_source_item(session: Session, item_id: int) -> None:
    session.scalar(select(SourceItem).where(SourceItem.id == item_id).with_for_update())


def _latest(session: Session, item_id: int) -> VideoSummaryEvent | None:
    return session.scalars(
        select(VideoSummaryEvent)
        .where(VideoSummaryEvent.item_id == item_id)
        .order_by(VideoSummaryEvent.version.desc())
        .limit(1)
    ).first()


def _review_state(session: Session, item_id: int) -> tuple[int, int, str]:
    rows = session.execute(
        select(Viewpoint, Entity.canonical_name)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.source_item_id == item_id)
        .order_by(Viewpoint.id)
    ).all()
    pending = sum(v.verification_status in ("candidate", "needs_review") for v, _ in rows)
    confirmed = [(v, name) for v, name in rows if v.verification_status == "confirmed"]
    evidence = [
        [v.id, v.claim, v.stance, v.horizon, v.entity_id, v.entity_raw, name]
        for v, name in confirmed
    ]
    item = session.get(SourceItem, item_id)
    source = _video_context(session, item) if item else None
    fingerprint = hashlib.sha256(
        json.dumps([evidence, source], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return pending, len(confirmed), fingerprint


def _video_context(session: Session, item: SourceItem) -> dict:
    row = session.execute(
        select(Creator.display_name, SourceAccount.platform)
        .join(SourceAccount, SourceAccount.creator_id == Creator.id)
        .where(SourceAccount.id == item.source_account_id)
    ).first()
    live = item.item_type == "live"
    return {
        "display_name": row[0] if row else None,
        "video_time": item.published_at.isoformat() if item.published_at else None,
        "time_basis": (
            ("live_started_at" if live else "published_at") if item.published_at else "unknown"
        ),
        "platform": row[1] if row else None,
        "title": item.title,
        "item_type": item.item_type,
    }


def serialize_event(event: VideoSummaryEvent) -> dict:
    return {"event_id": f"vsum-{event.id}", **event.payload_json}


def _record_event(
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


def current_event(session: Session, item_id: int) -> VideoSummaryEvent | None:
    event = _latest(session, item_id)
    if event is None or event.state != "ready":
        return None
    item = session.get(SourceItem, item_id)
    if item is None:
        return None
    pending, confirmed, fingerprint = _review_state(session, item_id)
    return event if confirmed and not pending and fingerprint == event.fingerprint else None


def withdraw_if_invalid(
    session: Session, item_id: int, *, force: bool = False
) -> VideoSummaryEvent | None:
    """Call after any material review or source change, before its transaction commits."""
    _lock_source_item(session, item_id)
    _lock_event_order(session)
    previous = _latest(session, item_id)
    if previous is None or previous.state != "ready":
        return None
    item = session.get(SourceItem, item_id)
    pending, confirmed, fingerprint = _review_state(session, item_id) if item else (1, 0, "")
    if not force and item and confirmed and not pending and fingerprint == previous.fingerprint:
        return None
    prior = previous.payload_json
    payload = {
        **prior,
        "version": previous.version + 1,
        "state": "withdrawn",
        "summary": None,
        "withdraws_version": previous.version,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return _record_event(session, item_id, previous.version + 1, "withdrawn", None, payload)


def record_ready_summary(
    session: Session, item: SourceItem, *, expected_fingerprint: str | None = None
) -> VideoSummaryEvent:
    """Freeze one eligible generated summary in the same transaction as SourceItem."""
    _lock_source_item(session, item.id)
    _lock_event_order(session)
    pending, confirmed, fingerprint = _review_state(session, item.id)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise ValueError("生成期间观点或视频来源已变化，请重新生成总结")
    if pending or not confirmed or not (item.viewpoint_summary or "").strip():
        raise ValueError("视频总结尚未具备对外交付资格")
    withdraw_if_invalid(session, item.id)
    previous = _latest(session, item.id)
    version = (previous.version if previous else 0) + 1
    now = datetime.now(UTC)
    payload = {
        "item_id": item.id,
        "version": version,
        "state": "ready",
        **_video_context(session, item),
        "summary": item.viewpoint_summary,
        "generated_at": (
            item.summary_generated_at.isoformat() if item.summary_generated_at else now.isoformat()
        ),
        "updated_at": now.isoformat(),
    }
    return _record_event(session, item.id, version, "ready", fingerprint, payload)


def erase_deleted_item_history(session: Session, item_id: int) -> VideoSummaryEvent | None:
    """Manual deletion erases exported content but leaves a minimal withdrawal marker."""
    _lock_source_item(session, item_id)
    _lock_event_order(session)
    previous = _latest(session, item_id)
    if previous is None:
        return None
    session.execute(delete(VideoSummaryEvent).where(VideoSummaryEvent.item_id == item_id))
    session.flush()
    version = previous.version + 1
    return _record_event(
        session,
        item_id,
        version,
        "withdrawn",
        None,
        {
            "item_id": item_id,
            "version": version,
            "state": "withdrawn",
            "summary": None,
            "display_name": None,
            "video_time": None,
            "time_basis": "unknown",
            "withdraws_version": previous.version,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
