"""房间在线人数提取单测（用户 2026-10-08 需求）。

数据源事实：douyinLive 转发的 WebcastRoomStatsMessage，protojson int64 为字符串
形态（"780"），采集器按 envelope 全量落盘 jsonl（生产 1029 实录样本）。
"""

import json
from pathlib import Path

from app.services.danmaku import room_stats
from app.services.danmaku.collector import build_envelope
from app.services.danmaku.room_stats import latest_viewer_count


def _stats_envelope(total: object, method: str = "WebcastRoomStatsMessage") -> str:
    return build_envelope(
        json.dumps(
            {
                "common": {"method": method, "msgId": "1", "roomId": "9"},
                "displayLong": f"{total}在线观众",
                "total": total,
                "method": method,
            },
            ensure_ascii=False,
        )
    )


def _chat_envelope() -> str:
    return build_envelope(
        json.dumps(
            {
                "common": {"method": "WebcastChatMessage", "msgId": "2"},
                "user": {"nickName": "u"},
                "content": "你好",
                "method": "WebcastChatMessage",
            },
            ensure_ascii=False,
        )
    )


def test_returns_latest_total(tmp_path: Path) -> None:
    f = tmp_path / "1029.jsonl"
    f.write_text(
        "\n".join([_chat_envelope(), _stats_envelope("780"), _chat_envelope(), _stats_envelope("7233")])
        + "\n"
    )
    assert latest_viewer_count(f) == 7233


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert latest_viewer_count(tmp_path / "nope.jsonl") is None


def test_no_stats_message_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text(_chat_envelope() + "\n")
    assert latest_viewer_count(f) is None


def test_empty_file_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text("")
    assert latest_viewer_count(f) is None


def test_truncated_tail_line_skipped(tmp_path: Path) -> None:
    """写入中被读的半行（无换行结尾/截断 JSON）跳过，取更早的完整统计。"""
    f = tmp_path / "a.jsonl"
    full = _stats_envelope("500") + "\n"
    partial = '{"v": 1, "received_at": "2026-10-08T04:00:00+00:00", "msg": {"method": "WebcastRoomSt'
    f.write_text(full + partial)
    assert latest_viewer_count(f) == 500


def test_string_and_int_totals_both_accepted(tmp_path: Path) -> None:
    f1 = tmp_path / "s.jsonl"
    f1.write_text(_stats_envelope("66") + "\n")
    assert latest_viewer_count(f1) == 66
    doc = json.loads(_stats_envelope("77"))
    doc["msg"]["total"] = 77  # 非 protojson 形态，防御性兼容
    f2 = tmp_path / "i.jsonl"
    f2.write_text(json.dumps(doc, ensure_ascii=False) + "\n")
    assert latest_viewer_count(f2) == 77


def test_dirty_total_skipped_to_previous_valid(tmp_path: Path) -> None:
    f = tmp_path / "a.jsonl"
    f.write_text(_stats_envelope("500") + "\n" + _stats_envelope("n/a") + "\n")
    assert latest_viewer_count(f) == 500


def test_other_method_lines_ignored(tmp_path: Path) -> None:
    """聊天消息里恰好含 'total' 字样的行不得误当统计（marker 需精确匹配 method）。"""
    f = tmp_path / "a.jsonl"
    f.write_text(_stats_envelope("780") + "\n" + _stats_envelope("9", method="WebcastChatMessage") + "\n")
    assert latest_viewer_count(f) == 780


def test_tail_window_seek_used(monkeypatch, tmp_path: Path) -> None:
    """大于尾部窗口的档案只读尾部（长场档案数 MB，避免每切片全量读）。"""
    monkeypatch.setattr(room_stats, "_TAIL_BYTES", 1024)
    f = tmp_path / "big.jsonl"
    pad = "\n".join(json.dumps({"pad": i}) for i in range(400))  # ~6KB 填充，超出窗口
    f.write_text(pad + "\n" + _stats_envelope("1234") + "\n")
    assert f.stat().st_size > 4096
    assert latest_viewer_count(f) == 1234


def _timed_stats_envelope(total: object, received_at: str) -> str:
    doc = json.loads(_stats_envelope(total))
    doc["received_at"] = received_at
    return json.dumps(doc, ensure_ascii=False)


def test_at_time_picks_stats_near_slice_time(tmp_path: Path) -> None:
    """按切片时刻取值：先取窗口内最新的统计（补转写/恢复也拿当时人数）。"""
    from datetime import datetime

    f = tmp_path / "a.jsonl"
    f.write_text(
        "\n".join(
            [
                _timed_stats_envelope("100", "2026-10-08T02:00:00+00:00"),
                _timed_stats_envelope("300", "2026-10-08T02:02:00+00:00"),
                _timed_stats_envelope("999", "2026-10-08T03:00:00+00:00"),  # 收尾后的停摆值
            ]
        )
        + "\n"
    )
    at = datetime.fromisoformat("2026-10-08T02:03:00+00:00")
    assert latest_viewer_count(f, at_time=at) == 300


def test_at_time_rejects_stale_and_future_stats(tmp_path: Path) -> None:
    """窗口外（过旧 >15min / 过新 >10min）的统计不取 → None（宁缺毋错）。"""
    from datetime import datetime

    f = tmp_path / "a.jsonl"
    f.write_text(_timed_stats_envelope("50", "2026-10-08T02:00:00+00:00") + "\n")
    late = datetime.fromisoformat("2026-10-08T02:30:00+00:00")  # 统计在 30min 前
    assert latest_viewer_count(f, at_time=late) is None

    f2 = tmp_path / "b.jsonl"
    f2.write_text(_timed_stats_envelope("70", "2026-10-08T02:20:00+00:00") + "\n")
    early = datetime.fromisoformat("2026-10-08T02:00:00+00:00")  # 统计在 20min 后
    assert latest_viewer_count(f2, at_time=early) is None


def test_no_at_time_returns_latest_ignoring_age(tmp_path: Path) -> None:
    """不传 at_time 保持旧语义：最新一条（监控面板「当前人气」用）。"""
    f = tmp_path / "a.jsonl"
    f.write_text(
        _timed_stats_envelope("111", "2020-01-01T00:00:00+00:00")
        + "\n"
        + _timed_stats_envelope("222", "2020-01-01T01:00:00+00:00")
        + "\n"
    )
    assert latest_viewer_count(f) == 222
