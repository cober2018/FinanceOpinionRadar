"""直播值守状态聚合（Plan #4 后续：前端监控看板数据源）。

三个数据源拼一行状态：
  DB                值守账号字段 + 最近 live 会话（状态/分片/转录段/最近活动）
  recordings.json   值守桥同步状态（房间是否已写入录制器配置）
  StreamCap 日志    最近一次 per-room 开播探测（is_live / anchor_name，60s 内新鲜；
                    docker 不可达（如容器化部署）时 is_live=None，页面显示未知而非报错）
"""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import AppSetting, Creator, SourceAccount, SourceItem, TranscriptSegment

_ROOM_ID_RE = re.compile(r"live\.douyin\.com/(\d+)")
_STREAM_DATA_RE = re.compile(
    r"anchor_name='(?P<anchor>[^']*)', is_live=(?P<live>True|False)"
    r".*?live_url='(?P<url>[^']*)'"
)
_STATUS_CN = {
    "discovered": "发现",
    "resolved": "已解析",
    "media_ready": "媒体就绪",
    "transcribing": "转写中",
    "transcribed": "已收尾",
    "failed": "失败",
}


def streamcap_live_states() -> dict[str, tuple[bool | None, str]]:
    """StreamCap 日志 → {room_url: (is_live, anchor_name)}，每房间取最近一次探测。"""
    try:
        proc = subprocess.run(
            ["docker", "logs", "streamcap", "--since", "15m"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        # docker logs 把容器输出写在 stderr（stdout 只承载 docker 自身消息）
        log = proc.stdout + proc.stderr
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}
    states: dict[str, tuple[bool | None, str]] = {}
    for m in _STREAM_DATA_RE.finditer(log):
        states[m["url"]] = (m["live"] == "True", m["anchor"])
    return states


def _latest_live_session(session: Session, account_id: int) -> SourceItem | None:
    return (
        session.query(SourceItem)
        .filter(SourceItem.source_account_id == account_id, SourceItem.item_type == "live")
        .order_by(SourceItem.id.desc())
        .first()
    )


def build_live_monitors(session: Session) -> list[dict]:
    """监控看板行：所有 douyin 账号（含未值守的，前端据此渲染开关）。"""
    s = get_settings()
    accounts = session.scalars(
        select(SourceAccount).where(SourceAccount.platform == "douyin").order_by(SourceAccount.id)
    ).all()
    names: dict[int, str] = {
        cid: name for cid, name in session.query(Creator.id, Creator.display_name).all()
    }
    recorder_rooms = _recorder_rooms(s.recorder_config_path)
    live_states = streamcap_live_states()

    items = (
        session.query(SourceItem)
        .filter(
            SourceItem.item_type == "live",
            SourceItem.source_account_id.in_([a.id for a in accounts] or [0]),
        )
        .order_by(SourceItem.id.desc())
        .all()
    )
    latest: dict[int, SourceItem] = {}
    for it in items:  # id 降序 → 首见即最近会话
        latest.setdefault(it.source_account_id, it)
    counts: dict[int, int] = {
        item_id: n
        for item_id, n in session.query(TranscriptSegment.source_item_id, func.count())
        .filter(TranscriptSegment.source_item_id.in_([i.id for i in latest.values()] or [0]))
        .group_by(TranscriptSegment.source_item_id)
        .all()
    }

    rows = []
    for a in accounts:
        room = a.live_room_url or (a.url if a.url and "live.douyin.com/" in a.url else None)
        m = _ROOM_ID_RE.search(room) if room else None
        is_live, anchor = live_states.get(room, (None, "")) if room else (None, "")
        sess_item = latest.get(a.id)
        live_meta = (sess_item.metadata_json or {}).get("live") or {} if sess_item else {}
        name = names.get(a.creator_id)
        if not name or name == "未知来源":
            name = anchor or name or f"账号{a.id}"
        rows.append(
            {
                "account_id": a.id,
                "display_name": name,
                "profile_url": None if (a.url and "live.douyin.com/" in a.url) else a.url,
                "live_room_url": room,
                "room_id": m.group(1) if m else None,
                "enabled": a.enabled,
                "discovery_mode": a.discovery_mode,
                "poll_interval_sec": a.poll_interval_sec,
                "poll_interval_min_sec": a.poll_interval_min_sec,
                "poll_interval_max_sec": a.poll_interval_max_sec,
                "live_monitor_enabled": a.live_monitor_enabled,
                "monitor_interval_sec": a.monitor_interval_sec,
                "recorder_synced": room in recorder_rooms if room else False,
                "is_live": is_live,
                "session_status": sess_item.status if sess_item else None,
                "session_closed": bool(live_meta.get("closed")),
                "segment_count": live_meta.get("segment_count", 0),
                "transcript_count": counts.get(sess_item.id, 0) if sess_item else 0,
                "last_segment_at": live_meta.get("last_segment_at"),
            }
        )
    return rows


def _recorder_rooms(path_str: str) -> set[str]:
    if not path_str:
        return set()
    path = Path(path_str)
    if not path.exists():
        return set()
    try:
        return {r.get("url", "") for r in json.loads(path.read_text()) if isinstance(r, dict)}
    except (json.JSONDecodeError, OSError):
        return set()


# --- 安全设置（防风控类目）：app_setting 单键 JSON，env 是默认、DB 覆盖 ---

SECURITY_KEY = "security"
_SECURITY_DEFAULTS: dict[str, object] = {
    "discover_dispatch_stagger_max_sec": None,  # None = 用 env 默认
    "douyin_discover_max_pages": None,
    "proxy_pool": [],  # 预留：下载/解析出口代理池（radar 侧接线挂账 EPIC-04+）
    "review_confidence_threshold": None,  # 观点自动复核阈值（DB 覆盖 env）
    "review_min_evidence_chars": None,  # 自动复核证据最小字数（同上）
}


def get_security_settings(session: Session) -> dict:
    s = get_settings()
    row = session.get(AppSetting, SECURITY_KEY)
    stored = (row.value if row else {}) or {}
    merged: dict[str, object] = dict(_SECURITY_DEFAULTS)
    merged.update(stored)
    # None → 回落 env 当前值（前端展示"生效值"）
    merged["effective"] = {
        "discover_dispatch_stagger_max_sec": merged["discover_dispatch_stagger_max_sec"]
        if merged["discover_dispatch_stagger_max_sec"] is not None
        else s.discover_dispatch_stagger_max_sec,
        "douyin_discover_max_pages": merged["douyin_discover_max_pages"]
        if merged["douyin_discover_max_pages"] is not None
        else s.douyin_discover_max_pages,
        "review_confidence_threshold": merged["review_confidence_threshold"]
        if merged["review_confidence_threshold"] is not None
        else s.review_confidence_threshold,
        "review_min_evidence_chars": merged["review_min_evidence_chars"]
        if merged["review_min_evidence_chars"] is not None
        else s.review_min_evidence_chars,
    }
    return merged


def put_security_settings(session: Session, payload: dict) -> dict:
    """校验 + 落库；未显式传入的字段保留 DB 现值（qg 自动维护会写 proxy_pool，
    部分保存不能把它清掉）。非法值 422 由路由层转 HTTPException。"""
    row = session.get(AppSetting, SECURITY_KEY)
    stored = (row.value if row else {}) or {}
    clean: dict[str, object] = {
        **_SECURITY_DEFAULTS,
        **{k: v for k, v in stored.items() if k in _SECURITY_DEFAULTS},
    }
    stagger = payload.get("discover_dispatch_stagger_max_sec")
    if stagger is not None:
        if not isinstance(stagger, int) or not 0 <= stagger <= 3600:
            raise ValueError("discover_dispatch_stagger_max_sec 需为 0–3600 的整数")
        clean["discover_dispatch_stagger_max_sec"] = stagger
    pages = payload.get("douyin_discover_max_pages")
    if pages is not None:
        if not isinstance(pages, int) or not 1 <= pages <= 20:
            raise ValueError("douyin_discover_max_pages 需为 1–20 的整数")
        clean["douyin_discover_max_pages"] = pages
    pool = payload.get("proxy_pool")
    if pool is not None:
        if not isinstance(pool, list) or len(pool) > 20 or not all(
            isinstance(p, str) and p.startswith(("http://", "socks5://")) for p in pool
        ):
            raise ValueError("proxy_pool 需为 http(s)/socks5 URL 列表（≤20 条）")
        clean["proxy_pool"] = pool
    thr = payload.get("review_confidence_threshold")
    if thr is not None:
        if not isinstance(thr, (int, float)) or not 0.5 <= float(thr) <= 1.0:
            raise ValueError("review_confidence_threshold 需为 0.5–1.0 的数值")
        clean["review_confidence_threshold"] = float(thr)
    min_chars = payload.get("review_min_evidence_chars")
    if min_chars is not None:
        if not isinstance(min_chars, int) or not 0 <= min_chars <= 2000:
            raise ValueError("review_min_evidence_chars 需为 0–2000 的整数")
        clean["review_min_evidence_chars"] = min_chars
    row = session.get(AppSetting, SECURITY_KEY)
    if row is None:
        row = AppSetting(key=SECURITY_KEY, value=clean)
        session.add(row)
    else:
        row.value = clean
    session.commit()
    return get_security_settings(session)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
