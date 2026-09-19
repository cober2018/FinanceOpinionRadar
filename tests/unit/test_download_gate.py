"""出口并发闸单测：抢位/上限/释放/TTL 过期回收/排队超时。

依赖本机 Redis（celery broker 同实例）；不可用时 skip，不阻塞 CI。
键用每次运行的唯一前缀，防与其他运行/worker 互踩。
"""

import time
import uuid

import pytest
import redis as redis_lib
from app.core.settings import get_settings
from app.services.download_gate import DownloadGate, EgressBusyError


@pytest.fixture()
def gate() -> tuple[DownloadGate, str]:
    try:
        client = redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=2)
        client.ping()
    except redis_lib.RedisError as exc:  # pragma: no cover - 本地无 redis 才走
        pytest.skip(f"redis 不可用，跳过出口闸测试: {exc}")
    egress = f"unittest:{uuid.uuid4().hex}"
    yield DownloadGate(client), egress
    client.delete(f"radar:egress-gate:{egress}")


def test_acquire_under_limit_then_full(gate: tuple[DownloadGate, str]) -> None:
    g, egress = gate
    tokens = [g.try_acquire(egress, limit=2, ttl_sec=60) for _ in range(2)]
    assert all(t is not None for t in tokens)
    assert g.try_acquire(egress, limit=2, ttl_sec=60) is None  # 满载


def test_release_frees_slot(gate: tuple[DownloadGate, str]) -> None:
    g, egress = gate
    t1 = g.try_acquire(egress, limit=1, ttl_sec=60)
    t2 = g.try_acquire(egress, limit=1, ttl_sec=60)
    assert t1 is not None and t2 is None
    g.release(egress, t1)
    assert g.try_acquire(egress, limit=1, ttl_sec=60) is not None


def test_stale_slots_expire_out(gate: tuple[DownloadGate, str]) -> None:
    """崩溃遗留位次（score 老于 TTL）在下次抢位时被清掉，不永久占坑。"""
    g, egress = gate
    client = g._client
    stale_token = "stale-token"
    client.zadd(
        f"radar:egress-gate:{egress}", {stale_token: time.time() - 3600}
    )
    token = g.try_acquire(egress, limit=1, ttl_sec=60)
    assert token is not None  # 过期位被清，新位拿到
    assert client.zscore(f"radar:egress-gate:{egress}", stale_token) is None


def test_slot_releases_on_exception(gate: tuple[DownloadGate, str]) -> None:
    g, egress = gate
    with pytest.raises(RuntimeError), g.slot(egress, limit=1, ttl_sec=60):
        raise RuntimeError("下载中途崩")
    assert g.try_acquire(egress, limit=1, ttl_sec=60) is not None  # 位次已释放


def test_acquire_timeout_raises_busy(gate: tuple[DownloadGate, str]) -> None:
    g, egress = gate
    assert g.try_acquire(egress, limit=1, ttl_sec=60) is not None
    with pytest.raises(EgressBusyError):
        g.acquire(egress, limit=1, ttl_sec=60, timeout_sec=1.0)


def test_blocking_acquire_succeeds_after_release(gate: tuple[DownloadGate, str]) -> None:
    g, egress = gate
    t = g.try_acquire(egress, limit=1, ttl_sec=60)

    def _later() -> None:
        time.sleep(1.2)
        g.release(egress, t)

    import threading

    th = threading.Thread(target=_later)
    th.start()
    token = g.acquire(egress, limit=1, ttl_sec=60, timeout_sec=5.0)
    th.join()
    assert token is not None
