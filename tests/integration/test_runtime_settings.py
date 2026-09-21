"""系统服务开关集成测试（Plan #7）：/settings/runtime + launchd 对账。

launchctl 是宿主机副作用面，测试里整体替换 _apply_flag / _installed_state 两个
接缝；DB 意图存取、合并语义、对账逻辑走真实 Postgres（radar_test）。
"""

import pytest
from app.db.models import AppSetting
from app.db.session import get_db
from app.main import app
from app.services import runtime_control
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def fake_launchctl(monkeypatch):
    """记录 apply 调用并维护假的 launchd 加载态。"""
    calls: list[tuple[str, bool]] = []
    installed: dict[str, bool | None] = {"autostart": False, "watchdog": False}

    def fake_apply(flag: str, enabled: bool) -> None:
        calls.append((flag, enabled))
        installed[flag] = enabled

    monkeypatch.setattr(runtime_control, "_apply_flag", fake_apply)
    monkeypatch.setattr(runtime_control, "_installed_state", lambda: dict(installed))
    return calls, installed


def test_get_defaults_without_db_row(client):
    res = client.get("/api/v1/settings/runtime")
    assert res.status_code == 200
    body = res.json()
    assert body["autostart"] is False
    assert body["watchdog"] is False
    assert body["installed"] == {"autostart": False, "watchdog": False}


def test_put_persists_intent_and_applies(client, db_session, fake_launchctl):
    calls, _ = fake_launchctl
    res = client.put("/api/v1/settings/runtime", json={"autostart": True})
    assert res.status_code == 200
    body = res.json()
    assert body["autostart"] is True
    assert calls == [("autostart", True)]

    row = db_session.get(AppSetting, runtime_control.RUNTIME_KEY)
    assert row is not None
    assert row.value["autostart"] is True
    assert row.value["watchdog"] is False


def test_put_merges_partial_payload(client, fake_launchctl):
    calls, _ = fake_launchctl
    assert client.put("/api/v1/settings/runtime", json={"autostart": True}).status_code == 200
    res = client.put("/api/v1/settings/runtime", json={"watchdog": True})
    assert res.status_code == 200
    body = res.json()
    assert body["autostart"] is True  # 未提到的开关保持原值
    assert body["watchdog"] is True
    assert calls == [("autostart", True), ("watchdog", True)]


def test_put_same_value_skips_apply(client, fake_launchctl):
    calls, _ = fake_launchctl
    client.put("/api/v1/settings/runtime", json={"autostart": True})
    calls.clear()
    res = client.put("/api/v1/settings/runtime", json={"autostart": True})
    assert res.status_code == 200
    assert calls == []  # 值没变不重复 bootstrap/bootout


def test_put_false_boots_out(client, fake_launchctl):
    calls, _ = fake_launchctl
    client.put("/api/v1/settings/runtime", json={"watchdog": True})
    calls.clear()
    res = client.put("/api/v1/settings/runtime", json={"watchdog": False})
    assert res.status_code == 200
    assert res.json()["watchdog"] is False
    assert calls == [("watchdog", False)]


def test_put_invalid_type_422(client, fake_launchctl):
    res = client.put("/api/v1/settings/runtime", json={"autostart": "yes"})
    assert res.status_code == 422


def test_apply_failure_surfaces_422(client, monkeypatch):
    def broken_apply(flag: str, enabled: bool) -> None:
        raise ValueError("launchctl bootstrap 失败（boom）")

    monkeypatch.setattr(runtime_control, "_apply_flag", broken_apply)
    res = client.put("/api/v1/settings/runtime", json={"autostart": True})
    assert res.status_code == 422
    assert "boom" in res.json()["detail"]


def test_sync_applies_db_intent(db_session, monkeypatch):
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        runtime_control, "_apply_flag", lambda flag, enabled: calls.append((flag, enabled))
    )
    db_session.add(AppSetting(key=runtime_control.RUNTIME_KEY, value={"autostart": True}))
    db_session.commit()

    runtime_control.sync_runtime_on_startup(db_session)
    assert ("autostart", True) in calls
    assert ("watchdog", False) in calls


def test_sync_never_raises(db_session, monkeypatch):
    def broken_apply(flag: str, enabled: bool) -> None:
        raise RuntimeError("launchctl missing")

    monkeypatch.setattr(runtime_control, "_apply_flag", broken_apply)
    runtime_control.sync_runtime_on_startup(db_session)  # 不抛即通过
