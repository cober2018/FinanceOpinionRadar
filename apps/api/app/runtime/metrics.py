"""轻量运行指标（2026-11-18 优化项 4）。

零第三方依赖的 Prometheus 文本格式暴露：
- 进程内计数（HTTP 请求）：API 进程内存即可（单进程，重启归零可接受）；
- 跨进程计数（celery 任务、身份池熔断）：经 Redis HINCRBY 聚合——API 与 worker
  是不同进程，内存互不可见（worker 重启不清零是顺带的优点）；
- 瞬时 gauge（队列深度/连接池/近 1h 任务结果）：/metrics 抓取时现场采集。

纪律：所有写入与 Redis 访问一律 best-effort（try/except pass）——指标绝不允许
影响请求或任务路径；抓取失败宁可少报不可报错。
"""

import threading
import time
import uuid
from collections import deque
from typing import Any

# Redis 键（与业务键空间隔离的前缀 radar:m:*)
_COUNTERS_KEY = "radar:m:counters"
_EXHAUSTION_KEY = "radar:m:identity_exhaustion"

_lock = threading.Lock()
_http_counters: dict[str, float] = {}
_http_latency: dict[str, float] = {}  # name → 累计秒（count 由同名 _total 提供）
_exhaustion_local: deque[float] = deque(maxlen=500)  # worker 侧 Redis 失败时的兜底窗口

_QUEUES = ("default", "media", "llm", "danmaku")


def _fmt_labels(labels: dict[str, str]) -> str:
    # Prometheus 文本格式：{k="v",...}，值转义 \ " \n；键序固定保证输出稳定
    if not labels:
        return ""
    parts = []
    for k in sorted(labels):
        v = str(labels[k]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{k}="{v}"')
    return "{" + ",".join(parts) + "}"


def inc_http(name: str, status: str, route: str, amount: float = 1.0) -> None:
    """API 进程内的 HTTP 计数（requests_total / latency_seconds_total）。"""
    key = f'{name}{_fmt_labels({"route": route, "status": status})}'
    with _lock:
        _http_counters[key] = _http_counters.get(key, 0) + amount


def add_http_latency(route: str, seconds: float) -> None:
    key = f'radar_http_request_seconds_total{{route="{route}"}}'
    with _lock:
        _http_latency[key] = _http_latency.get(key, 0) + seconds


def inc_shared(name: str, amount: int = 1, **labels: str) -> None:
    """跨进程计数（celery 任务等）：写 Redis HINCRBY，失败静默（指标不阻断任务）。

    HINCRBY 只支持整数：耗时类指标以毫秒整数累计（如 *_task_ms_total）。
    """
    field = f"{name}{_fmt_labels(labels)}"
    try:
        import redis as redis_lib

        from app.runtime.settings import get_settings

        r = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=1)
        try:
            r.hincrby(_COUNTERS_KEY, field, amount)
        finally:
            r.close()
    except Exception:  # noqa: BLE001 - 指标写入失败不影响业务
        pass


def note_identity_pool_exhaustion() -> None:
    """抖音身份池熔断事件（douyin_client 503/IDENTITY_POOL_EXHAUSTED 时调用）。

    ZSET score=时间戳：读取端按 1h 窗口 ZCOUNT，写入端顺手清理 7 天前旧事件。
    """
    now = time.time()
    try:
        import redis as redis_lib

        from app.runtime.settings import get_settings

        r = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=1)
        try:
            pipe = r.pipeline()
            pipe.zadd(_EXHAUSTION_KEY, {uuid.uuid4().hex: now})
            pipe.zremrangebyscore(_EXHAUSTION_KEY, 0, now - 7 * 86400)
            pipe.execute()
        finally:
            r.close()
    except Exception:  # noqa: BLE001
        with _lock:
            _exhaustion_local.append(now)


def _read_shared_counters() -> dict[str, float]:
    try:
        import redis as redis_lib

        from app.runtime.settings import get_settings

        r = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=1)
        try:
            raw: dict = r.hgetall(_COUNTERS_KEY)  # type: ignore[assignment]  # 同步客户端，存根给了 sync/async 联合
        finally:
            r.close()
        return {k.decode(): float(v) for k, v in raw.items()} if raw else {}
    except Exception:  # noqa: BLE001
        return {}


def _exhaustion_window_count() -> float:
    try:
        import redis as redis_lib

        from app.runtime.settings import get_settings

        r = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=1)
        try:
            window = (time.time() - 3600, time.time() + 1)
            n: Any = r.zcount(_EXHAUSTION_KEY, *window)  # 存根联合，运行时是 int
        finally:
            r.close()
        return float(n or 0)
    except Exception:  # noqa: BLE001
        cutoff = time.time() - 3600
        with _lock:
            return float(sum(1 for t in _exhaustion_local if t > cutoff))


def _queue_depths() -> dict[str, float]:
    try:
        import redis as redis_lib

        from app.runtime.settings import get_settings

        r = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=1)
        try:
            pipe = r.pipeline()
            for q in _QUEUES:
                pipe.llen(q)
            depths = pipe.execute()
        finally:
            r.close()
        return dict(zip(_QUEUES, (float(d or 0) for d in depths), strict=True))
    except Exception:  # noqa: BLE001
        return {}


def _db_pool_stats() -> dict[str, float]:
    try:
        from app.runtime.db.session import get_engine

        pool = get_engine().pool
        # QueuePool 才有这两个属性；存根基类没写，用 getattr 兜底任意池实现
        checked = getattr(pool, "checkedout", None)
        size = getattr(pool, "size", None)
        if checked is None or size is None:
            return {}
        return {"checkedout": float(checked), "size": float(size)}
    except Exception:  # noqa: BLE001
        return {}


def _job_run_window() -> dict[str, float]:
    try:
        from sqlalchemy import text

        from app.runtime.db.session import get_session_factory

        with get_session_factory()() as session:
            rows = session.execute(text(
                "SELECT status, count(*) FROM job_run "
                "WHERE created_at > now() - interval '1 hour' GROUP BY status"
            )).all()
        return {status: float(n) for status, n in rows}
    except Exception:  # noqa: BLE001
        return {}


def render() -> str:
    """组装 Prometheus 文本格式响应（0.0.4）。"""
    lines: list[str] = []

    def _emit_type(name: str, mtype: str, help_text: str) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")

    with _lock:
        http_snap = dict(_http_counters)
        latency_snap = dict(_http_latency)

    _emit_type("radar_http_requests_total", "counter", "HTTP requests by route and status")
    for key in sorted(http_snap):
        lines.append(f"{key} {http_snap[key]:g}")
    _emit_type("radar_http_request_seconds_total", "counter", "HTTP request latency sum in seconds")
    for key in sorted(latency_snap):
        lines.append(f"{key} {latency_snap[key]:g}")

    shared = _read_shared_counters()
    if shared:
        # 按指标族（{ 前的基础名）分组，每族一组 HELP/TYPE——文本格式的要求
        families: dict[str, list[str]] = {}
        for key in sorted(shared):
            families.setdefault(key.partition("{")[0], []).append(key)
        helps = {
            "radar_celery_tasks_total": "Celery task outcomes by name and state",
            "radar_celery_task_ms_total": "Celery task latency sum in milliseconds",
        }
        for base in sorted(families):
            _emit_type(base, "counter", helps.get(base, "Redis-aggregated counter"))
            for field in families[base]:
                lines.append(f"{field} {shared[field]:g}")

    _emit_type(
        "radar_identity_pool_exhaustion_1h", "gauge",
        "Douyin identity pool exhaustion events in the last hour",
    )
    lines.append(f"radar_identity_pool_exhaustion_1h {_exhaustion_window_count():g}")

    depths = _queue_depths()
    if depths:
        _emit_type("radar_queue_depth", "gauge", "Celery queue depths (LLEN)")
        for q in sorted(depths):
            lines.append(f'radar_queue_depth{{queue="{q}"}} {depths[q]:g}')

    pool = _db_pool_stats()
    if pool:
        _emit_type("radar_db_pool_checkedout", "gauge", "Checked-out DB connections in API process")
        lines.append(f"radar_db_pool_checkedout {pool['checkedout']:g}")

    jobs = _job_run_window()
    if jobs:
        _emit_type("radar_job_run_1h", "gauge", "job_run rows created in the last hour by status")
        for status in sorted(jobs):
            lines.append(f'radar_job_run_1h{{status="{status}"}} {jobs[status]:g}')

    lines.append("")
    return "\n".join(lines)
