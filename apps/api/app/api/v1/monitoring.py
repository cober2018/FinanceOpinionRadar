"""监控看板与安全设置 API（Plan #4 后续：前端"监控/安全"页数据面）。"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

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
