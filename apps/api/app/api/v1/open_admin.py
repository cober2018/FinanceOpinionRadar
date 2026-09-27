"""开放数据层管理端点（Plan #7，控制台内网）：API Key 与推送渠道管理。

明文 Key 仅创建响应回显一次；列表只显 prefix。创建/吊销/渠道变更写审计。
"""

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.open.deps import hash_key
from app.db.models import ApiKey, PushChannel, VideoSummaryDelivery, VideoSummaryEvent
from app.db.session import get_db
from app.services.audit import write_audit

router = APIRouter(prefix="/open-admin", tags=["open-admin"])

DbDep = Annotated[Session, Depends(get_db)]

_CHANNEL_TYPES = ("generic_webhook", "feishu", "dingtalk")


def _iso(v) -> str | None:
    return v.isoformat() if v is not None else None


def _serialize_key(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "status": k.status,
        "last_used_at": _iso(k.last_used_at),
        "created_at": _iso(k.created_at),
    }


def _serialize_channel(c: PushChannel) -> dict:
    cfg = c.config_json or {}
    return {
        "id": c.id,
        "name": c.name,
        "channel_type": c.channel_type,
        "url": cfg.get("url") or "",
        "has_secret": bool(cfg.get("secret")),
        "enabled": c.enabled,
        "summary_enabled": c.summary_enabled,
        "viewpoint_enabled": cfg.get("viewpoint_enabled", True),
        "created_at": _iso(c.created_at),
    }


# --- API Key ---


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/api-keys")
def list_api_keys(session: DbDep):
    rows = session.scalars(select(ApiKey).order_by(ApiKey.id.desc())).all()
    return {"items": [_serialize_key(k) for k in rows]}


@router.post("/api-keys", status_code=201)
def create_api_key(body: ApiKeyCreate, session: DbDep):
    raw = f"rk_{secrets.token_hex(16)}"
    row = ApiKey(
        name=body.name.strip(),
        key_hash=hash_key(raw),
        prefix=raw[:8],
    )
    session.add(row)
    session.flush()
    write_audit(
        session,
        actor="console",
        action="create",
        resource_type="api_key",
        resource_id=row.id,
        before=None,
        after={"name": row.name, "prefix": row.prefix},
    )
    session.commit()
    # 明文仅此一次回显
    return {"id": row.id, "name": row.name, "api_key": raw}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: int, session: DbDep):
    row = session.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"api_key {key_id} 不存在")
    before = {"status": row.status}
    row.status = "revoked"
    write_audit(
        session,
        actor="console",
        action="revoke",
        resource_type="api_key",
        resource_id=row.id,
        before=before,
        after={"status": "revoked"},
    )
    session.commit()
    return {"id": row.id, "status": "revoked"}


# --- 推送渠道 ---


class PushChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    channel_type: Literal["generic_webhook", "feishu", "dingtalk"]
    url: str = Field(min_length=1, max_length=500)
    secret: str | None = Field(None, max_length=200)
    viewpoint_enabled: bool = True


class PushChannelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    url: str | None = Field(None, min_length=1, max_length=500)
    secret: str | None = Field(None, max_length=200)
    enabled: bool | None = None
    summary_enabled: bool | None = None
    viewpoint_enabled: bool | None = None


@router.get("/push-channels")
def list_push_channels(session: DbDep):
    rows = session.scalars(select(PushChannel).order_by(PushChannel.id.desc())).all()
    return {"items": [_serialize_channel(c) for c in rows]}


@router.post("/push-channels", status_code=201)
def create_push_channel(body: PushChannelCreate, session: DbDep):
    if body.channel_type not in _CHANNEL_TYPES:
        raise HTTPException(status_code=422, detail=f"channel_type 必须是 {_CHANNEL_TYPES}")
    row = PushChannel(
        name=body.name.strip(),
        channel_type=body.channel_type,
        config_json={
            "url": body.url.strip(),
            "secret": body.secret or "",
            "viewpoint_enabled": body.viewpoint_enabled,
        },
        enabled=True,
    )
    session.add(row)
    session.flush()
    write_audit(
        session,
        actor="console",
        action="create",
        resource_type="push_channel",
        resource_id=row.id,
        before=None,
        after={"name": row.name, "channel_type": row.channel_type},
    )
    session.commit()
    return _serialize_channel(row)


@router.patch("/push-channels/{channel_id}")
def patch_push_channel(channel_id: int, body: PushChannelPatch, session: DbDep):
    row = session.get(PushChannel, channel_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"push_channel {channel_id} 不存在")
    before = _serialize_channel(row)
    updates = body.model_dump(exclude_unset=True)
    cfg = dict(row.config_json or {})
    if "name" in updates:
        row.name = updates["name"].strip()
    if "url" in updates:
        cfg["url"] = updates["url"].strip()
    if "secret" in updates:
        cfg["secret"] = updates["secret"] or ""
    if "viewpoint_enabled" in updates:
        cfg["viewpoint_enabled"] = updates["viewpoint_enabled"]
    if "enabled" in updates:
        row.enabled = updates["enabled"]
    summary_enabled = updates.get("summary_enabled", row.summary_enabled)
    if summary_enabled:
        if row.channel_type != "generic_webhook" or not cfg.get("secret"):
            raise HTTPException(
                status_code=422, detail="视频总结订阅仅支持配置签名密钥的通用 Webhook"
            )
        from app.services.video_summary_push import validate_target

        try:
            validate_target(str(cfg.get("url") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if "summary_enabled" in updates:
        if summary_enabled and not row.summary_enabled:
            cfg["summary_from_event_id"] = session.scalar(
                select(VideoSummaryEvent.id).order_by(VideoSummaryEvent.id.desc()).limit(1)
            ) or 0
        row.summary_enabled = updates["summary_enabled"]
    row.config_json = cfg
    write_audit(
        session,
        actor="console",
        action="patch",
        resource_type="push_channel",
        resource_id=row.id,
        before=before,
        after=_serialize_channel(row),
    )
    session.commit()
    return _serialize_channel(row)


@router.get("/video-summary-deliveries")
def list_video_summary_deliveries(
    session: DbDep,
    limit: int = Query(50, ge=1, le=200),
):
    rows = session.execute(
        select(VideoSummaryDelivery, PushChannel.name, VideoSummaryEvent)
        .join(PushChannel, PushChannel.id == VideoSummaryDelivery.channel_id)
        .join(VideoSummaryEvent, VideoSummaryEvent.id == VideoSummaryDelivery.event_id)
        .order_by(VideoSummaryDelivery.id.desc())
        .limit(limit)
    ).all()
    return {"items": [
        {
            "id": delivery.id,
            "channel_id": delivery.channel_id,
            "channel_name": channel_name,
            "event_id": f"vsum-{event.id}",
            "item_id": event.item_id,
            "version": event.version,
            "event_state": event.state,
            "status": delivery.status,
            "attempt": delivery.attempt,
            "last_http_status": delivery.last_http_status,
            "error": delivery.error,
            "updated_at": delivery.updated_at,
        }
        for delivery, channel_name, event in rows
    ]}


class SummaryDeliveryReconcile(BaseModel):
    outcome: Literal["sent", "retry"]
    note: str = Field(min_length=1, max_length=500)


@router.post("/video-summary-deliveries/{delivery_id}/reconcile")
def reconcile_summary_delivery(delivery_id: int, body: SummaryDeliveryReconcile, session: DbDep):
    delivery = session.get(VideoSummaryDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="视频总结投递不存在")
    if delivery.status not in ("unknown", "failed", "dead"):
        raise HTTPException(status_code=409, detail="只有未知或失败投递可对账")
    before = {"status": delivery.status, "attempt": delivery.attempt}
    delivery.status = "sent" if body.outcome == "sent" else "failed"
    if body.outcome == "retry":
        delivery.attempt = 0
    delivery.error = f"人工对账：{body.note.strip()}"[:500]
    write_audit(
        session,
        actor="console",
        action="reconcile",
        resource_type="video_summary_delivery",
        resource_id=delivery.id,
        before=before,
        after={"status": delivery.status, "note": body.note.strip()},
    )
    session.commit()
    return {"id": delivery.id, "status": delivery.status}


@router.delete("/push-channels/{channel_id}")
def delete_push_channel(channel_id: int, session: DbDep):
    row = session.get(PushChannel, channel_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"push_channel {channel_id} 不存在")
    write_audit(
        session,
        actor="console",
        action="delete",
        resource_type="push_channel",
        resource_id=row.id,
        before=_serialize_channel(row),
        after=None,
    )
    session.delete(row)
    session.commit()
    return {"id": channel_id, "deleted": True}


@router.post("/push-channels/{channel_id}/test")
def test_push_channel(channel_id: int, session: DbDep):
    """立即发一条测试消息验证渠道配置（含加签）。"""
    from app.services.push import send_test_message

    try:
        return send_test_message(session, channel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 网络失败把原因带回前端
        raise HTTPException(status_code=502, detail=f"推送测试失败：{str(exc)[:200]}") from exc
