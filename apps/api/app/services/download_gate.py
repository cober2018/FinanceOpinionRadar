"""出口并发闸：每个出口 IP 同时最多 N 个抖音网络操作（Redis 跨进程信号量）。

规则（2026-09-19 与用户对齐）：单 IP 并发上限 egress_max_concurrency（默认 3），
配置 M 个代理 IP 后总容量 ≈ (1 直连 + M) × N。防的是两类同源限流——
dtk 上游 429（抖音 API）与 douyinvod CDN 403（视频文件），两者出口都是本机/代理 IP。

实现：Celery worker 是多进程 prefork，进程内信号量无效，故用 Redis ZSET 原子信号量：
- acquire：Lua 脚本内「清过期位 → 判容量 → ZADD 抢位」三步原子执行，无竞态
- 位次带 score=时间戳，TTL（egress_slot_ttl_sec）兜底：worker 崩溃后遗留位次自动回收
- release：ZREM 自己的 token；重复 release 安全（幂等）
"""

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import redis as redis_lib
import structlog

from app.core.settings import get_settings

logger = structlog.get_logger(__name__)

# Lua：清过期(score < now-ttl) → 容量未满则占位。KEYS[1]=闸键
# ARGV: [1]过期线 [2]上限 [3]score(now) [4]token [5]key TTL(ms)
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
  redis.call('PEXPIRE', KEYS[1], ARGV[5])
  return 1
end
return 0
"""

DEFAULT_WAIT_TIMEOUT_SEC = 180.0
_POLL_INTERVAL_SEC = 0.5


class EgressBusyError(Exception):
    """排队超时仍拿不到闸位（出口全部满载）。上层按普通失败重试。"""


class DownloadGate:
    """按出口键（'direct' 或代理串）计数的并发闸。"""

    def __init__(self, client: redis_lib.Redis) -> None:
        self._client = client
        self._acquire = client.register_script(_ACQUIRE_LUA)

    @staticmethod
    def _key(egress: str) -> str:
        return f"radar:egress-gate:{egress}"

    def try_acquire(self, egress: str, *, limit: int, ttl_sec: int) -> str | None:
        """非阻塞抢位：成功返回 token，满载返回 None。"""
        token = uuid.uuid4().hex
        now = time.time()
        ok = self._acquire(
            keys=[self._key(egress)],
            args=[now - ttl_sec, limit, now, token, int(ttl_sec * 1000)],
        )
        return token if ok else None

    def release(self, egress: str, token: str) -> None:
        try:
            self._client.zrem(self._key(egress), token)
        except redis_lib.RedisError:
            logger.warning("egress_gate_release_failed", egress=egress)

    def acquire(
        self, egress: str, *, limit: int, ttl_sec: int, timeout_sec: float
    ) -> str:
        """阻塞抢位：轮询等待，超时抛 EgressBusyError。"""
        deadline = time.monotonic() + timeout_sec
        while True:
            token = self.try_acquire(egress, limit=limit, ttl_sec=ttl_sec)
            if token is not None:
                return token
            if time.monotonic() >= deadline:
                raise EgressBusyError(
                    f"出口 {egress} 并发闸排队超时（>{timeout_sec:.0f}s，上限 {limit}）"
                )
            time.sleep(_POLL_INTERVAL_SEC)

    @contextmanager
    def slot(
        self,
        egress: str,
        *,
        limit: int,
        ttl_sec: int,
        timeout_sec: float = DEFAULT_WAIT_TIMEOUT_SEC,
    ) -> Iterator[None]:
        """占位上下文：with gate.slot(egress, ...) as _: ... 退出即释放。

        Redis 不可用时 fail-open（放行不闸）——下载可用性优先于风控纪律。
        """
        try:
            token = self.acquire(
                egress, limit=limit, ttl_sec=ttl_sec, timeout_sec=timeout_sec
            )
        except redis_lib.RedisError:
            logger.warning("egress_gate_unavailable_fail_open", egress=egress)
            yield
            return
        try:
            yield
        finally:
            self.release(egress, token)


_gate: DownloadGate | None = None


def get_download_gate() -> DownloadGate:
    global _gate
    if _gate is None:
        client = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=5)
        _gate = DownloadGate(client)
    return _gate


def egress_limit() -> int:
    return get_settings().egress_max_concurrency
