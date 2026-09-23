"""开放数据层管理端点（Plan #7，控制台内网）：API Key 与推送渠道管理。

明文 Key 仅创建响应回显一次；列表只显 prefix。创建/吊销/渠道变更写审计。
"""

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.open.deps import hash_key
from app.db.models import ApiKey, PushChannel
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


class PushChannelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    url: str | None = Field(None, min_length=1, max_length=500)
    secret: str | None = Field(None, max_length=200)
    enabled: bool | None = None


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
        config_json={"url": body.url.strip(), "secret": body.secret or ""},
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
    if "enabled" in updates:
        row.enabled = updates["enabled"]
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
