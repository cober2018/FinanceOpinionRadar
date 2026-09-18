"""弹幕采集派发器（Plan #5 Task 4）。

候选 = douyin + enabled + live_monitor_enabled 账号的 transcribing live 会话（F1：
采集窗口绑定录制会话，douyinLive 连接数受实际直播数约束）。sink 文件 mtime 新鲜
（< heartbeat_stale_sec）视为采集器在跑（F2 软闸，采集器零 DB 写的存活信号）；
活跃数 ≥ max_collectors 不再派新（F7 容量）。派发只投 celery 任务，零直接 IO。
"""

import time
from collections.abc import Callable
from pathlib import Path

import structlog

from app.core.settings import get_settings
from app.db.models import SourceAccount, SourceItem
from app.services.danmaku.collector import sink_path
from app.services.recorder_bridge import resolve_room_id

logger = structlog.get_logger(__name__)


def dispatch_danmaku_collectors(
    session,
    *,
    repo=None,
    sender: Callable[..., object] | None = None,
    settings=None,
    now: Callable[[], float] = time.time,
) -> dict:
    """beat 入口：候选会话 → 新鲜度过滤 → 容量内派发。"""
    s = settings or get_settings()
    if not s.danmaku_ws_base_url or not s.danmaku_sink_dir:
        logger.info("danmaku_dispatch_noop", reason="danmaku 未配置")
        return {"noop": "danmaku 未配置"}
    if sender is None:
        from app.worker.celery_app import celery_app

        sender = celery_app.send_task

    candidates = (
        session.query(SourceItem)
        .join(SourceAccount, SourceAccount.id == SourceItem.source_account_id)
        .filter(
            SourceItem.item_type == "live",
            SourceItem.status == "transcribing",
            SourceAccount.platform == "douyin",
            SourceAccount.enabled.is_(True),
            SourceAccount.live_monitor_enabled.is_(True),
        )
        .order_by(SourceItem.id)
        .all()
    )
    active = 0
    dispatched = 0
    no_room = 0
    for item in candidates:
        account = session.get(SourceAccount, item.source_account_id)
        room_id = resolve_room_id(account) if account else None
        if room_id is None:
            no_room += 1
            logger.warning("danmaku_dispatch_no_room_id", item_id=item.id)
            continue
        path = sink_path(s.danmaku_sink_dir, item.external_item_id, item.id)
        if _collector_alive(path, s.danmaku_heartbeat_stale_sec, now):
            active += 1
            continue
        if active + dispatched >= s.danmaku_max_collectors:
            continue  # 容量已满：不再派发，但继续统计（no_room/active 计数与顺序无关）
        sender("collect_danmaku", args=[item.id, room_id])
        dispatched += 1
    result = {
        "candidates": len(candidates),
        "active": active,
        "dispatched": dispatched,
        "no_room": no_room,
    }
    logger.info("danmaku_dispatch_scan", **result)
    return result


def _collector_alive(path: Path, stale_sec: int, now: Callable[[], float]) -> bool:
    try:
        return (now() - path.stat().st_mtime) <= stale_sec
    except OSError:  # 文件不存在/不可读 = 采集器不在
        return False
