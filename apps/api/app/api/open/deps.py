"""开放 API 鉴权与调用审计（Plan #7）。

- require_api_key：X-API-Key → sha256 查 active key，更新 last_used_at；
  key id 存 request.state 供审计归因（鉴权失败为 None 也记账）。
- audit_middleware：仅拦 /open/ 前缀；独立短 session 写 api_call_log，
  不与请求级 get_db 会话互相污染；写日志自身失败只告警不阻断响应。
"""

import hashlib
import time
from datetime import UTC, datetime

import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import ApiCallLog, ApiKey
from app.db.session import get_db, get_session_factory

logger = structlog.get_logger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def require_api_key(
    request: Request,
    raw_key: str | None = Depends(_api_key_header),
    session: Session = Depends(get_db),
) -> ApiKey:
    """开放端点统一鉴权：有效 key 挂到 request.state 供审计归因。"""
    key_row: ApiKey | None = None
    if raw_key:
        key_row = session.scalars(
            select(ApiKey).where(
                ApiKey.key_hash == hash_key(raw_key), ApiKey.status == "active"
            )
        ).first()
    if key_row is None:
        request.state.api_key_id = None
        raise HTTPException(status_code=401, detail="无效或已吊销的 API Key")
    session.execute(
        update(ApiKey)
        .where(ApiKey.id == key_row.id)
        .values(last_used_at=datetime.now(UTC))
    )
    session.commit()
    request.state.api_key_id = key_row.id
    return key_row


async def audit_middleware(request: Request, call_next):
    """仅 /open/ 前缀记账：method/path/status/latency/key_id（鉴权失败 key_id 空）。"""
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        _record_call(request, 500, start)
        raise
    if request.url.path.startswith("/open/"):
        _record_call(request, response.status_code, start)
    return response


def audit_session() -> Session:
    """审计独立会话工厂；测试 monkeypatch 指向测试库，避免写开发库。"""
    return get_session_factory()()


def _record_call(request: Request, status_code: int, start: float) -> None:
    latency_ms = int((time.monotonic() - start) * 1000)
    try:
        with audit_session() as session:
            session.add(
                ApiCallLog(
                    api_key_id=getattr(request.state, "api_key_id", None),
                    method=request.method,
                    path=request.url.path[:300],
                    status_code=status_code,
                    latency_ms=latency_ms,
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 审计失败不阻断业务响应
        logger.warning(
            "api_call_audit_failed", path=request.url.path[:120], error=str(exc)[:150]
        )
