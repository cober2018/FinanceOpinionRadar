"""青果长效代理：池同步纯逻辑 + 客户端异常映射（MockTransport，不出网）。"""

import httpx
import pytest
from app.services.qg_proxy import (
    QgLongtermClient,
    QgProxyError,
    _prefix,
    proxy_url,
    remove_servers_from_pool,
    sync_pool_into_settings,
)


class _FakeSession:
    """最小 session 替身：AppSetting 读写 + get_security_settings 依赖同一存储。"""

    def __init__(self, store: dict) -> None:
        self.store = store
        self.committed = 0

    def get(self, model, key):
        return self.store.get(key)

    def commit(self) -> None:
        self.committed += 1


def _session_with_pool(pool: list[str]) -> tuple[object, dict]:
    store = {
        "security": type("Row", (), {"key": "security", "value": {"proxy_pool": pool}})(),
        "proxy_provider_qg": None,
    }
    return _FakeSession(store), store


def test_sync_pool_preserves_manual_entries() -> None:
    session, store = _session_with_pool(
        [_prefix("KEY1", "PWD1") + "old.qg.net:1", "http://127.0.0.1:7890"]
    )
    updated = sync_pool_into_settings(
        session, ["tunpool-jdqh4.qg.net:17142"], "KEY1", "PWD1"
    )
    assert "http://127.0.0.1:7890" in updated  # 手工出口保留
    assert proxy_url("tunpool-jdqh4.qg.net:17142", "KEY1", "PWD1") in updated
    assert _prefix("KEY1", "PWD1") + "old.qg.net:1" not in updated  # 同凭证旧 qg 条目被替换
    assert store["security"].value["proxy_pool"] == updated


def test_sync_pool_replaces_only_own_prefix() -> None:
    session, _ = _session_with_pool(
        [_prefix("KEY1", "PWD1") + "a.qg.net:1", "http://127.0.0.1:7890"]
    )
    updated = sync_pool_into_settings(session, ["b.qg.net:2"], "KEY1", "PWD1")
    assert updated == ["http://127.0.0.1:7890", _prefix("KEY1", "PWD1") + "b.qg.net:2"]


def test_remove_servers_from_pool() -> None:
    session, _ = _session_with_pool(
        [
            _prefix("KEY1", "PWD1") + "a.qg.net:1",
            _prefix("KEY1", "PWD1") + "b.qg.net:2",
            "http://127.0.0.1:7890",
        ]
    )
    updated = remove_servers_from_pool(session, {"a.qg.net:1"}, "KEY1", "PWD1")
    assert _prefix("KEY1", "PWD1") + "a.qg.net:1" not in updated
    assert _prefix("KEY1", "PWD1") + "b.qg.net:2" in updated


def _client_with(payload: dict) -> QgLongtermClient:
    c = QgLongtermClient("KEY1", "PWD1")
    c._client = httpx.Client(
        base_url="https://longterm.proxy.qg.net",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
    return c


def test_client_maps_error_code() -> None:
    with pytest.raises(QgProxyError) as ei:
        _client_with(
            {"code": "NO_AVAILABLE_CHANNEL", "message": "没有可用的空闲通道"}
        ).query()
    assert ei.value.code == "NO_AVAILABLE_CHANNEL"


def test_client_query_parses_servers() -> None:
    ips = _client_with(
        {"code": "SUCCESS", "data": [{"server": "tunpool-jdqh4.qg.net:17142", "distinct": False}]}
    ).query()
    assert ips[0]["server"] == "tunpool-jdqh4.qg.net:17142"


def test_sync_with_empty_reconciles_expired_entries_out() -> None:
    """自动维护语义：查询为空（到期/释放）时，同凭证条目清出池，手工出口保留。"""
    session, _ = _session_with_pool(
        [
            _prefix("KEY1", "PWD1") + "expired.qg.net:9",
            "http://127.0.0.1:7890",
        ]
    )
    updated = sync_pool_into_settings(session, [], "KEY1", "PWD1")
    assert updated == ["http://127.0.0.1:7890"]
