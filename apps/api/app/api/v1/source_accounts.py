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
    # 值守直播间 URL（live.douyin.com/<room_id>）；与主页 url 分离，可同时配两路
    live_room_url: HttpUrl | None = None
    config_json: dict = {}


class PatchSourceAccountRequest(BaseModel):
    url: HttpUrl | None = None
    discovery_mode: Literal["manual", "auto_poll"] | None = None
    poll_interval_sec: int | None = None
    poll_interval_min_sec: int | None = None
    poll_interval_max_sec: int | None = None
    live_room_url: HttpUrl | None = None
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
    live_room_url: str | None
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


_LIVE_ROOM_RE = re.compile(r"https?://live\.douyin\.com/\d+/?$")


def _ensure_live_room_url(url: str) -> None:
    """douyin 值守直播间必须是房间页形态（StreamCap handler 只认该形态）。"""
    if not _LIVE_ROOM_RE.match(url):
        raise HTTPException(
            status_code=422,
            detail="直播间 URL 需为 https://live.douyin.com/<room_id> 房间页形态",
        )


def _reject_bad_profile_url(url: str) -> None:
    raise HTTPException(
        status_code=422,
        detail="抖音账号 URL 需为主页形态 https://www.douyin.com/user/<sec_uid>",
    )


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
    if body.live_room_url is not None:
        _ensure_live_room_url(str(body.live_room_url))
        account.live_room_url = str(body.live_room_url)
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
        if field == "live_room_url" and value is not None:
            _ensure_live_room_url(value)
            value = str(value)
        elif field == "url" and value is not None:
            value = str(value)
            if account.platform == "douyin":  # 主页形态校验 + sec_uid 重锚（与注册同规则）
                m = _SEC_UID_RE.search(value)
                if m is None:
                    _reject_bad_profile_url(value)  # raises 422
                    return  # 不可达，仅为类型收窄
                account.external_id = m.group(1)
        setattr(account, field, value)
    session.commit()
    return account


@router.delete("/{account_id}", status_code=200)
def delete_source_account(
    account_id: int,
    session: DbDep,
    mode: str = "delete_content",  # delete_content | keep_none
):
    """删除主播账号及其全部内容（级联：条目/转录/观点/资产，FK CASCADE）。

    删除后值守桥下一轮同步自动从录制器配置移除该房间；账号不存在返回 404。
    """
    account = session.get(SourceAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"source_account {account_id} 不存在")
    from app.services.audit import write_audit

    write_audit(
        session,
        actor="console",
        action="delete",
        resource_type="source_account",
        resource_id=account_id,
        before={
            "platform": account.platform,
            "external_id": account.external_id,
            "live_monitor_enabled": account.live_monitor_enabled,
        },
        after=None,
        reason=f"用户删除主播（mode={mode}）",
    )
    session.delete(account)  # FK CASCADE：source_item → transcript/viewpoint/asset
    session.commit()
    return {"deleted": account_id}
