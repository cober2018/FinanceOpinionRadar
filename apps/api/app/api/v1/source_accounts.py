"""账号管理 API（Plan #4 Task 3，RAD-LIVE-04 入口）：注册/列表/局部更新。"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import SourceAccount
from app.db.session import get_db
from app.repositories.source_accounts import SourceAccountRepository
from app.services import discovery
from app.services.media.url_guard import UrlNotAllowedError, ensure_allowed_url

router = APIRouter(prefix="/source-accounts", tags=["source-accounts"])

DbDep = Annotated[Session, Depends(get_db)]

# douyin 主页 sec_uid（与 adapter 侧一致的账号归一锚点，核对项②）
_SEC_UID_RE = re.compile(r"/user/(MS4w[\w-]+)")


class CreateSourceAccountRequest(BaseModel):
    platform: str
    url: HttpUrl
    display_name: str | None = None
    discovery_mode: Literal["manual", "auto_poll"] = "manual"
    poll_interval_sec: int = 3600
    # 人类化随机区间（秒）：设置后每次发现成功在区间内重抽 poll_interval_sec
    poll_interval_min_sec: int | None = None
    poll_interval_max_sec: int | None = None
    config_json: dict = {}


class PatchSourceAccountRequest(BaseModel):
    discovery_mode: Literal["manual", "auto_poll"] | None = None
    poll_interval_sec: int | None = None
    poll_interval_min_sec: int | None = None
    poll_interval_max_sec: int | None = None
    enabled: bool | None = None
    live_monitor_enabled: bool | None = None
    monitor_interval_sec: int | None = None
    expected_schedule: dict[str, Any] | None = None


class SourceAccountResponse(BaseModel):
    id: int
    creator_id: int
    platform: str
    external_id: str
    handle: str | None
    url: str | None
    discovery_mode: str
    poll_interval_sec: int
    poll_interval_min_sec: int | None
    poll_interval_max_sec: int | None
    enabled: bool
    live_monitor_enabled: bool
    monitor_interval_sec: int
    expected_schedule: dict[str, Any] | None
    last_success_at: datetime | None
    failure_count: int

    model_config = {"from_attributes": True}


def _derive_external_id(platform: str, url: str) -> str:
    """douyin 从主页 URL 提取 sec_uid（账号归一的关键，核对项②）；其余平台以 URL 为身份。"""
    if platform == "douyin":
        m = _SEC_UID_RE.search(url)
        if m is None:
            raise HTTPException(
                status_code=422,
                detail="抖音账号 URL 需为主页形态 https://www.douyin.com/user/<sec_uid>",
            )
        return m.group(1)
    return url


@router.post("", response_model=SourceAccountResponse, status_code=201)
def create_source_account(body: CreateSourceAccountRequest, session: DbDep):
    url = str(body.url)
    try:
        ensure_allowed_url(url, get_settings().media_host_allowlist)
    except UrlNotAllowedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    external_id = _derive_external_id(body.platform, url)

    creator = discovery.get_or_create_creator(session, body.display_name)
    account = SourceAccountRepository(session).upsert_by_external(
        creator_id=creator.id,
        platform=body.platform,
        external_id=external_id,
        url=url,
        discovery_mode=body.discovery_mode,
    )
    account.poll_interval_sec = body.poll_interval_sec
    account.poll_interval_min_sec = body.poll_interval_min_sec
    account.poll_interval_max_sec = body.poll_interval_max_sec
    account.config_json = body.config_json
    session.commit()
    return account


@router.get("", response_model=list[SourceAccountResponse])
def list_source_accounts(
    session: DbDep,
    platform: str | None = None,
    enabled: bool | None = None,
):
    stmt = select(SourceAccount).order_by(SourceAccount.id)
    if platform is not None:
        stmt = stmt.where(SourceAccount.platform == platform)
    if enabled is not None:
        stmt = stmt.where(SourceAccount.enabled.is_(enabled))
    return list(session.scalars(stmt).all())


@router.patch("/{account_id}", response_model=SourceAccountResponse)
def patch_source_account(account_id: int, body: PatchSourceAccountRequest, session: DbDep):
    account = session.get(SourceAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"source_account {account_id} 不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    session.commit()
    return account
