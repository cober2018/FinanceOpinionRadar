"""直播值守看板（Plan #4 后续）：每个值守直播间一行的终端表格。

数据来源（零额外抖音请求）：
  DB                source_account 值守字段 + 最近 live 会话（状态/分片/转录段）
  recordings.json   值守桥同步状态（房间是否已写入录制器配置）
  StreamCap 日志    最近一次 per-room 开播探测（is_live / anchor_name，60s 内新鲜）

用法：make live-status（或 .venv/bin/python scripts/live_dashboard.py）
"""

import json
import re
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.db.models import Creator, SourceAccount, SourceItem, TranscriptSegment
from app.db.session import get_session_factory
from sqlalchemy import select

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


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "FW" else 1 for c in str(s))


def _pad(s: object, w: int) -> str:
    s = str(s)
    return s + " " * max(0, w - _width(s))


def _ago(iso: str | None) -> str:
    if not iso:
        return "-"
    dt = datetime.fromisoformat(iso)
    secs = int((datetime.now(UTC) - dt).total_seconds())
    secs = max(secs, 0)
    if secs < 3600:
        return f"{secs // 60}分钟前"
    if secs < 86400:
        return f"{secs // 3600}小时{secs % 60 // 10}0分前"
    return f"{secs // 86400}天前"


def _streamcap_live_states() -> dict[str, tuple[bool, str]]:
    """StreamCap 日志 → {room_url: (is_live, anchor_name)}，取每个房间最近一次探测。"""
    try:
        proc = subprocess.run(
            ["docker", "logs", "streamcap", "--since", "15m"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        # docker logs 把容器输出写在 stderr（stdout 只承载 docker 自身消息）
        log = proc.stdout + proc.stderr
    except (subprocess.SubprocessError, FileNotFoundError):
        return {}
    states: dict[str, tuple[bool, str]] = {}
    for m in _STREAM_DATA_RE.finditer(log):
        states[m["url"]] = (m["live"] == "True", m["anchor"])
    return states


def _latest_sessions(session) -> dict[int, SourceItem]:
    items = session.scalars(
        select(SourceItem)
        .where(SourceItem.item_type == "live")
        .order_by(SourceItem.id.desc())
    ).all()
    latest: dict[int, SourceItem] = {}
    for it in items:  # id 降序 → 首见即最近
        latest.setdefault(it.source_account_id, it)
    return latest


def main() -> None:
    s = get_settings()
    factory = get_session_factory()
    with factory() as session:
        accounts = session.scalars(
            select(SourceAccount).where(SourceAccount.platform == "douyin")
            .order_by(SourceAccount.id)
        ).all()
        creators = {
            c.id: c.display_name
            for c in session.scalars(select(Creator)).all()
        }
        latest = _latest_sessions(session)
        # 按 item 聚合转录段数
        from sqlalchemy import func

        counts = dict(
            session.query(TranscriptSegment.source_item_id, func.count())
            .filter(
                TranscriptSegment.source_item_id.in_([i.id for i in latest.values()] or [0])
            )
            .group_by(TranscriptSegment.source_item_id)
            .all()
        )

        recorder_rooms: set[str] = set()
        if s.recorder_config_path and Path(s.recorder_config_path).exists():
            try:
                recorder_rooms = {
                    r.get("url", "")
                    for r in json.loads(Path(s.recorder_config_path).read_text())
                }
            except (json.JSONDecodeError, OSError):
                pass
        live_states = _streamcap_live_states()

        headers = ["主播", "直播间", "值守", "录制器", "在播", "最近会话", "分片/转录段", "最近活动"]
        rows: list[list[str]] = []
        for a in accounts:
            if not a.live_monitor_enabled:
                continue
            m = _ROOM_ID_RE.search(a.url or "")
            room = m.group(1) if m else (a.url or "-")
            is_room = bool(m)
            synced = "✓" if (is_room and a.url in recorder_rooms) else ("✗ 非房间URL" if not is_room else "✗ 未同步")
            is_live, anchor = live_states.get(a.url, (None, ""))
            live_s = {True: "🔴 在播", False: "未播", None: "?"}[is_live]
            it = latest.get(a.id)
            if it is None:
                sess, segs, act = "暂无会话", "-", "-"
            else:
                live_meta = (it.metadata_json or {}).get("live") or {}
                sess = _STATUS_CN.get(it.status, it.status)
                if live_meta.get("closed"):
                    sess += "(closed)"
                segs = f"{live_meta.get('segment_count', 0)}/{counts.get(it.id, 0)}"
                act = _ago(live_meta.get("last_segment_at"))
            name = creators.get(a.creator_id)
            if not name or name == "未知来源":
                name = anchor or name or "-"
            rows.append([
                f"{name}#{a.id}", room, f"{a.monitor_interval_sec}s", synced,
                live_s, sess, segs, act,
            ])

    widths = [max(_width(r[i]) for r in rows + [headers]) for i in range(len(headers))]
    print(_pad(headers[0], widths[0] + 2), end="")
    for h, w in zip(headers[1:], widths[1:]):
        print(_pad(h, w + 2), end="")
    print()
    for r in rows:
        line = "".join(_pad(c, w + 2) for c, w in zip(r, widths))
        print(line.rstrip())
    if not rows:
        print("(无值守账号：PATCH /source-accounts/<id> {\"live_monitor_enabled\":true} 开启)")


if __name__ == "__main__":
    main()
