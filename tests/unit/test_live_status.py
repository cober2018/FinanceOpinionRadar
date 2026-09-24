"""在播状态合成（张心朔 9-24 实录）：录制中的房间无探测日志，靠会话事实补判。"""

from datetime import UTC, datetime, timedelta

from app.services.live_status import _derive_is_live

NOW = datetime.now(UTC)


def test_probe_true_wins():
    assert _derive_is_live(True, None, None, NOW) is True


def test_recording_session_with_fresh_segment_is_live():
    """探测行缺失（None/False）+ transcribing + 分片新鲜 → 在播。"""
    fresh = (NOW - timedelta(minutes=3)).isoformat()
    assert _derive_is_live(None, "transcribing", fresh, NOW) is True
    assert _derive_is_live(False, "transcribing", fresh, NOW) is True


def test_stale_transcribing_not_live():
    """transcribing 但分片停滞超窗口 → 不补判（录制实质中断）。"""
    stale = (NOW - timedelta(minutes=20)).isoformat()
    assert _derive_is_live(None, "transcribing", stale, NOW) is None


def test_future_timestamp_ignored():
    """时钟偏移导致的未来时间不判在播。"""
    future = (NOW + timedelta(minutes=5)).isoformat()
    assert _derive_is_live(None, "transcribing", future, NOW) is None


def test_closed_session_keeps_probe_result():
    assert _derive_is_live(None, "transcribed", (NOW - timedelta(minutes=1)).isoformat(), NOW) is None
    assert _derive_is_live(False, "ready", None, NOW) is False
    assert _derive_is_live(None, None, None, NOW) is None
