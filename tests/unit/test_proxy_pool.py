"""代理池单测：稳定绑定 / 冷却跳过 / 空池直连。"""


from app.services.proxy_pool import ProxyPool


class _Session:
    pass


def _patch_settings(monkeypatch, pool):
    from app.services import live_status

    monkeypatch.setattr(
        live_status, "get_security_settings", lambda s: {"proxy_pool": pool}
    )


def test_empty_pool_returns_none(monkeypatch):
    _patch_settings(monkeypatch, [])
    assert ProxyPool().pick(_Session(), "acc1") is None


def test_stable_binding_same_key_same_proxy(monkeypatch):
    _patch_settings(monkeypatch, ["http://a:1", "http://b:2", "http://c:3"])
    p = ProxyPool()
    picks = {p.pick(_Session(), "acc7") for _ in range(10)}
    assert len(picks) == 1  # 同 key 恒定


def test_failure_cools_down_and_pool_degrades(monkeypatch):
    _patch_settings(monkeypatch, ["http://a:1", "http://b:2"])
    p = ProxyPool()
    key = "acc9"
    first = p.pick(_Session(), key)
    p.report_failure(first, "403")
    second = p.pick(_Session(), key)
    assert second != first  # 冷却中跳过 → 落到另一个
    p.report_failure(second, "403")
    assert p.pick(_Session(), key) is None  # 全冷却 → 直连
    assert p.pick(_Session(), "other") is None  # 冷却状态进程内共享


def test_success_clears_cooldown(monkeypatch):
    _patch_settings(monkeypatch, ["http://a:1"])
    p = ProxyPool()
    first = p.pick(_Session(), "k")
    p.report_failure(first, "x")
    assert p.pick(_Session(), "k2") is None
    p.report_success(first)
    assert p.pick(_Session(), "k2") == first
