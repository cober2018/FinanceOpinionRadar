"""弹幕采集 worker（Plan #5 Task 3）。

单会话长任务：pg advisory lock（session 级，连接断开自动释放 = 崩溃自愈，F2 硬闸）
→ 连 douyinLive ws://…/ws/<room_id> → 每条消息以 envelope 追加 jsonl（F3 原始档案）
→ 退出条件（F6：会话收尾/ROOM_NOT_FOUND/寿命上限/连接失败重试至会话结束）。
全程零 DB 写（F2：避开与 live_ingest 的 metadata_json 整列覆盖竞态）；仅周期读
item.status 判会话收尾。WS 工厂/时钟/sleep 全部可注入，测试零网络。
"""

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import text
from websocket import WebSocketException, WebSocketTimeoutException, create_connection

from app.core.settings import get_settings
from app.db.session import get_session_factory
from app.services.danmaku.parse import classify, is_room_not_found, loads_line

logger = structlog.get_logger(__name__)

_ADVISORY_LOCK_BASE = 907_300_000  # danmaku collector 键空间（+ item_id）
_RECV_TIMEOUT_SEC = 5  # recv 超时即回到 housekeeping 节拍
_HOUSEKEEPING_TICKS = 6  # ~30s（6 × 5s recv 超时）：ping + 状态轮询 + 心跳 touch
_RECONNECT_SEC = 15
_SINK_VERSION = 1


def _lock_key(item_id: int) -> int:
    return _ADVISORY_LOCK_BASE + item_id


def sink_path(sink_dir: str | Path, external_item_id: str, item_id: int) -> Path:
    """F4：<sink_dir>/<会话键日期段>/<item_id>.jsonl。文件名 radar 自产，无外部输入。"""
    date = external_item_id.rsplit(":", 1)[-1]
    return Path(sink_dir) / date / f"{item_id}.jsonl"


def build_envelope(raw_line: str) -> str:
    """F3：全量原始档案行。可解析则存 msg 对象（ingest 直用），否则存 raw 原文。"""
    doc = loads_line(raw_line)
    envelope: dict[str, object] = {
        "v": _SINK_VERSION,
        "received_at": datetime.now(UTC).isoformat(),
    }
    if doc is not None:
        envelope["msg"] = doc
    else:
        envelope["raw"] = raw_line
    return json.dumps(envelope, ensure_ascii=False)


def _default_ws_factory(url: str):
    return create_connection(url, timeout=_RECV_TIMEOUT_SEC)


def _session_ended(session, item_id: int) -> bool:
    status = session.execute(
        text("SELECT status FROM source_item WHERE id = :i"), {"i": item_id}
    ).scalar()
    return status is None or status != "transcribing"


def _heartbeat(sink: Path) -> None:
    try:
        os.utime(sink)  # 无消息期也让派发器看到采集器存活（F2 软闸）
    except OSError:
        pass


def collect_danmaku(
    item_id: int,
    room_id: str,
    *,
    ws_factory=None,
    session_factory=None,
    settings=None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """采集主入口（celery 任务体）。返回 {collected, connects, exit_reason} 或 skip/noop。"""
    s = settings or get_settings()
    if not s.danmaku_ws_base_url or not s.danmaku_sink_dir:
        logger.info("danmaku_collect_noop", reason="danmaku 未配置")
        return {"noop": "danmaku 未配置"}
    # 注入约定：session_factory() → Session（默认两段式 get_session_factory()()，同 live_ingest）
    factory = session_factory or (lambda: get_session_factory()())
    ws_factory = ws_factory or _default_ws_factory

    session = factory()
    started = clock()
    try:
        locked = session.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(item_id)}
        ).scalar()
        session.commit()
        if not locked:
            logger.info("danmaku_collect_skipped_singleton", item_id=item_id)
            return {"skipped": "singleton"}
        row = session.execute(
            text("SELECT external_item_id FROM source_item WHERE id = :i"), {"i": item_id}
        ).first()
        if row is None:
            return {"skipped": "item_missing"}
        sink = sink_path(s.danmaku_sink_dir, row[0], item_id)
        sink.parent.mkdir(parents=True, exist_ok=True)
        return _collect_loop(
            item_id, room_id, sink, session, s, ws_factory, sleep, clock, started
        )
    finally:
        try:
            session.rollback()
            session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(item_id)})
            session.commit()
        except Exception:  # noqa: BLE001  连接已坏时锁随连接释放，尽力而为
            pass
        session.close()


def _collect_loop(
    item_id: int,
    room_id: str,
    sink: Path,
    session,
    s,
    ws_factory,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    started: float,
) -> dict:
    url = f"{s.danmaku_ws_base_url.rstrip('/')}/ws/{room_id}"
    collected = 0
    connects = 0
    exit_reason: str | None = None
    with open(sink, "a", encoding="utf-8") as fh:
        while exit_reason is None:
            if _session_ended(session, item_id):
                exit_reason = "session_closed"
                break
            if clock() - started >= s.danmaku_collector_max_duration_sec:
                exit_reason = "max_duration"
                break
            try:
                ws = ws_factory(url)
            except Exception as exc:  # noqa: BLE001  服务不可达：重试至会话结束（F6）
                logger.warning("danmaku_ws_connect_failed", error=str(exc)[:200])
                sleep(_RECONNECT_SEC)
                continue
            connects += 1
            outcome, count = _pump(ws, fh, sink, session, item_id, clock, started, s)
            collected += count
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            if outcome != "disconnect":
                exit_reason = outcome
                break
            sleep(_RECONNECT_SEC)
    logger.info(
        "danmaku_collect_done", item_id=item_id, room_id=room_id,
        collected=collected, connects=connects, exit_reason=exit_reason,
    )
    return {"collected": collected, "connects": connects, "exit_reason": exit_reason}


def _pump(ws, fh, sink: Path, session, item_id: int, clock, started: float, s) -> tuple[str, int]:
    """单连接收流循环。返回 (outcome, 本连接消息数)：disconnect 回外层重连，其余退出。"""
    ws.settimeout(_RECV_TIMEOUT_SEC)
    stale_ticks = 0
    count = 0
    while True:
        try:
            raw = ws.recv()
        except WebSocketTimeoutException:
            stale_ticks += 1
            if stale_ticks < _HOUSEKEEPING_TICKS:
                continue
            stale_ticks = 0
            try:
                ws.send("ping")  # douyinLive 客户端保活约定（30s 文本 ping）
            except Exception:  # noqa: BLE001
                return "disconnect", count
            if _session_ended(session, item_id):
                return "session_closed", count
            if clock() - started >= s.danmaku_collector_max_duration_sec:
                return "max_duration", count
            _heartbeat(sink)
            continue
        except (WebSocketException, OSError):
            return "disconnect", count
        if not isinstance(raw, str) or not raw:
            continue
        fh.write(build_envelope(raw) + "\n")
        fh.flush()
        count += 1
        doc = loads_line(raw)
        if doc is not None and classify(doc) == "system" and is_room_not_found(doc):
            return "room_not_found", count
