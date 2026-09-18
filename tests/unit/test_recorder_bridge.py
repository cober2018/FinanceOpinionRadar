"""recorder_bridge 单测（Plan #4 Task 6）：夹紧/URL 归一/原子写/重启触发/节段隔离。

StreamCap v1.0.3 集成契约见 Task 1 决策记录：recordings.json 不热重载 →
仅变化时原子写 + docker restart；不变则不动（防重启风暴）。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services import recorder_bridge


def _settings(tmp_path: Path, **over):
    base = {
        "recorder_config_path": str(tmp_path / "config" / "recordings.json"),
        "recorder_container_name": "streamcap",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **over) -> Path:
    s = _settings(tmp_path, **over)
    monkeypatch.setattr(recorder_bridge, "get_settings", lambda: s)
    return Path(s.recorder_config_path)


def _account(**over):
    account = SimpleNamespace(
        id=1,
        platform="douyin",
        external_id="MS4wLjABxxx",
        url="http://live.douyin.com/330698468897",
        monitor_interval_sec=300,
    )
    for k, v in over.items():
        setattr(account, k, v)
    return account


# --- G2：夹紧边界 ---


def test_clamp_segment_time_bounds():
    assert recorder_bridge.clamp_segment_time(300) == 300  # 下边界
    assert recorder_bridge.clamp_segment_time(600) == 600  # 上边界
    assert recorder_bridge.clamp_segment_time(250) == 300  # 越界下 → 夹到 300
    assert recorder_bridge.clamp_segment_time(900) == 600  # 越界上 → 夹到 600
    assert recorder_bridge.clamp_segment_time(450) == 450  # 区间内原样


def test_build_recording_normalizes_https_and_segment_time():
    rec = recorder_bridge.build_recording(_account(monitor_interval_sec=900))
    # 抖音 handler 只认 https（Task 1 实录）
    assert rec["url"] == "https://live.douyin.com/330698468897"
    assert rec["segment_time"] == 600
    assert rec["record_format"] == "TS"
    assert rec["segment_record"] is True
    assert rec["monitor_status"] is True
    # rec_id 确定性（同 URL 同 id）→ diff 天然稳定，不产生重启风暴
    assert rec["rec_id"] == recorder_bridge.build_recording(_account())["rec_id"]


# --- sync_live_monitors ---


def test_sync_noop_when_path_unconfigured(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _set(monkeypatch, tmp_path, recorder_config_path="")
    result = recorder_bridge.sync_live_monitors(Mock())
    assert result["noop"] == "recorder_config_path 未配置"


def test_sync_writes_atomically_and_restarts_on_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    path = _set(monkeypatch, tmp_path)
    repo = Mock()
    repo.list_live_monitored.return_value = [_account()]
    restarts: list[list[str]] = []
    monkeypatch.setattr(
        recorder_bridge.subprocess,
        "run",
        lambda argv, **kw: restarts.append(argv),
    )

    result = recorder_bridge.sync_live_monitors(Mock(), repo=repo)
    assert result["changed"] is True
    assert result["monitors"] == 1
    written = json.loads(path.read_text())
    assert written[0]["url"].startswith("https://")
    assert not list(path.parent.glob("*.tmp"))  # tmp+rename 原子性
    assert restarts == [["docker", "restart", "streamcap"]]


def test_sync_no_restart_when_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = _set(monkeypatch, tmp_path)
    repo = Mock()
    repo.list_live_monitored.return_value = [_account()]
    calls: list[list[str]] = []
    monkeypatch.setattr(recorder_bridge.subprocess, "run", lambda argv, **kw: calls.append(argv))

    recorder_bridge.sync_live_monitors(Mock(), repo=repo)  # 第一轮：写 + 重启
    before = path.read_bytes()
    result = recorder_bridge.sync_live_monitors(Mock(), repo=repo)  # 第二轮：不变
    assert result["changed"] is False
    assert path.read_bytes() == before
    assert calls == [["docker", "restart", "streamcap"]]  # 仅第一轮重启


def test_sync_keeps_rec_id_for_existing_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = _set(monkeypatch, tmp_path)
    old = [recorder_bridge.build_recording(_account())]
    old[0]["rec_id"] = "hand-written-id"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(old, ensure_ascii=False))

    repo = Mock()
    repo.list_live_monitored.return_value = [_account()]
    recorder_bridge.sync_live_monitors(Mock(), repo=repo)
    assert json.loads(path.read_text())[0]["rec_id"] == "hand-written-id"


def test_sync_user_settings_only_created_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    path = _set(monkeypatch, tmp_path)
    repo = Mock()
    repo.list_live_monitored.return_value = []
    # E1 节段隔离：bridge 只补缺失文件，不覆盖用户已有设置
    path.parent.mkdir(parents=True)
    user_settings = path.parent / "user_settings.json"
    user_settings.write_text('{"login_required": false, "loop_time_seconds": "120"}')

    recorder_bridge.sync_live_monitors(Mock(), repo=repo)
    assert json.loads(user_settings.read_text())["loop_time_seconds"] == "120"


def test_resolve_room_prefers_live_room_url_over_profile():
    account = _account(
        url="https://www.douyin.com/user/MS4wLjABxxx",
        live_room_url="http://live.douyin.com/2040437791",
    )
    # live_room_url 优先且 http→https 归一；主页 URL 不再被当直播间
    assert (
        recorder_bridge.resolve_room_url(account) == "https://live.douyin.com/2040437791"
    )


def test_resolve_room_id_extracts_digits_for_danmaku():
    # Plan #5：douyinLive 订阅用纯数字房间号（live_room_url 优先语义同 resolve_room_url）
    account = _account(
        url="https://www.douyin.com/user/MS4wLjABxxx",
        live_room_url="https://live.douyin.com/2040437791",
    )
    assert recorder_bridge.resolve_room_id(account) == "2040437791"
    assert recorder_bridge.resolve_room_id(_account()) == "330698468897"
    # 主页 URL 解析不出房间号 → None
    assert (
        recorder_bridge.resolve_room_id(_account(url="https://www.douyin.com/user/x"))
        is None
    )


def test_sync_skips_account_without_room_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = _set(monkeypatch, tmp_path)
    repo = Mock()
    # 只有主页 URL（live_room_url 未配）→ 跳过，不写也不重启
    repo.list_live_monitored.return_value = [_account(url="https://www.douyin.com/user/MS4wLjABxxx")]
    calls: list[list[str]] = []
    monkeypatch.setattr(recorder_bridge.subprocess, "run", lambda argv, **kw: calls.append(argv))

    result = recorder_bridge.sync_live_monitors(Mock(), repo=repo)
    assert result["changed"] is False and result["monitors"] == 0
    assert not path.exists()
    assert calls == []
