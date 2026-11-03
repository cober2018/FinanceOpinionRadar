"""来源账号身份锚点（sources 域入口）：URL 形态校验、稳定身份提取与注册查重。

供 sources API（入口校验）与 workflows.upsert_source_account（跨域用例）共用。
校验失败沿用 HTTPException 语义（FastAPI 在路由调用栈任意层捕获）。
"""

import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.sources.models import SourceAccount
from app.runtime.settings import get_settings

# douyin 主页 sec_uid（与 adapter 侧一致的账号归一锚点，核对项②）
SEC_UID_RE = re.compile(r"/user/(MS4w[\w-]+)")


def resolve_youtube_channel_id(url: str) -> str | None:
    """yt-dlp 解析 @handle → channel_id（UC 开头）。失败返回 None（跳过跨形态查重）。"""
    import subprocess

    try:
        binary = get_settings().ytdlp_binary or "yt-dlp"
        proc = subprocess.run(
            [binary, "--print", "channel_url", "--skip-download", url],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        m = re.search(r"channel/(UC[\w-]{10,})", line)
        if m:
            return m.group(1)
    return None


def channel_identity(platform: str, url: str) -> str | None:
    """从 URL 提取频道稳定身份：YouTube=channel_id（UC 开头，@handle 解析不出则返回
    handle 本身）；B 站=space 数字 mid。用于注册查重（同一频道多形态 URL 视为同一身份）。"""
    if platform == "youtube":
        m = re.search(r"channel/(UC[\w-]{10,})", url)
        if m:
            return m.group(1)
        m = re.search(r"/@([A-Za-z0-9._-]+)", url)
        if m:
            return f"@{m.group(1)}"
    elif platform == "bilibili":
        m = re.search(r"space\.bilibili\.com/(\d+)", url)
        if m:
            return m.group(1)
    return None


def reject_duplicate_channel(session: Session, platform: str, identity: str) -> None:
    """同频道已注册 → 409（账号可能有多种 URL 形态，跨 external_id/url 匹配）。"""
    dup = (
        session.query(SourceAccount)
        .filter(
            SourceAccount.platform == platform,
            (SourceAccount.external_id == identity)
            | (SourceAccount.url.ilike(f"%{identity}%")),
        )
        .first()
    )
    if dup is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该频道已注册为账号 #{dup.id}（{identity}），"
                "请勿重复添加；如需更换 URL 请编辑原账号"
            ),
        )


def derive_external_id(platform: str, url: str) -> str:
    """douyin 从主页 URL 提取 sec_uid（账号归一的关键，核对项②）；其余平台以 URL 为身份。"""
    if platform == "douyin":
        m = SEC_UID_RE.search(url)
        if m is None:
            raise HTTPException(
                status_code=422,
                detail="抖音账号 URL 需为主页形态 https://www.douyin.com/user/<sec_uid>",
            )
        return m.group(1)
    return url
