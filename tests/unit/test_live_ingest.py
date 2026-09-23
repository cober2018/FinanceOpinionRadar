"""live_ingest 纯逻辑单测（Plan #4 Task 5 Step 1）：目录扫描/分组/序号解析。

DB 相关行为（会话建立/幂等/单飞闸/收尾）在 tests/integration/test_live_ingest_flow.py
（conftest 作用域决定，unit 目录拿不到 db_session）。
"""

from pathlib import Path

from app.services import live_ingest


def _make_tree(root: Path) -> Path:
    """StreamCap 实录布局：<root>/<author>/<YYYY-MM-DD>/<base>_NNN.TS（folder_name_platform 关）。"""
    d = root / "新闻联播" / "2026-09-17"
    d.mkdir(parents=True)
    return d


def _make_tree_with_platform(root: Path) -> Path:
    """StreamCap 默认布局（folder_name_platform 开，bridge user_settings 默认）：
    <root>/<platform>/<author>/<YYYY-MM-DD>/<base>_NNN.TS——Task 1 决策记录，Task 6-Step3 实录。"""
    d = root / "抖音" / "MS4wLjABxxx" / "2026-09-17"
    d.mkdir(parents=True)
    return d


def test_scan_streamcap_default_layout_with_platform_dir(tmp_path: Path) -> None:
    day = _make_tree_with_platform(tmp_path)
    _write_seg(day, "base", 1)
    _write_seg(day, "base", 0)

    sessions = live_ingest.scan_live_dir(tmp_path)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.author == "MS4wLjABxxx"  # author 取主播级目录，平台级不进会话键
    assert s.date == "2026-09-17"
    assert [seg.index for seg in s.segments] == [0, 1]


def test_scan_reconnect_numbering_reset_does_not_collide(tmp_path: Path) -> None:
    """流重连后 StreamCap 每个 base 重新从 _000 计数（Task 6-Step3 真栈实录）：
    index 必须按 base 时间戳+序号合成，保序且唯一，否则重连分片被幂等去重吞掉。"""
    day = _make_tree_with_platform(tmp_path)
    p0 = _write_seg(day, "b_2026-09-17_22-22-02", 0)
    p1 = _write_seg(day, "b_2026-09-17_22-26-38", 0)
    p2 = _write_seg(day, "b_2026-09-17_22-26-38", 1)

    sessions = live_ingest.scan_live_dir(tmp_path)
    assert [seg.path for seg in sessions[0].segments] == [p0, p1, p2]
    assert len({seg.index for seg in sessions[0].segments}) == 3


def _write_seg(day_dir: Path, base: str, index: int) -> Path:
    p = day_dir / f"{base}_{index:03d}.TS"  # StreamCap 大写扩展名 + _%03d（Task 1 实录）
    p.write_bytes(b"\x00" * 16)
    return p


def test_scan_groups_by_author_and_date_sorted_by_index(tmp_path: Path) -> None:
    day = _make_tree(tmp_path)
    _write_seg(day, "base", 2)
    _write_seg(day, "base", 0)
    _write_seg(day, "base", 1)

    sessions = live_ingest.scan_live_dir(tmp_path)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.author == "新闻联播"
    assert s.date == "2026-09-17"
    assert [seg.index for seg in s.segments] == [0, 1, 2]


def test_scan_ignores_non_segment_files_and_wrong_depth(tmp_path: Path) -> None:
    day = _make_tree(tmp_path)
    _write_seg(day, "base", 0)
    (day / "notes.txt").write_text("x")
    (day / "base_part.tmp").write_bytes(b"x")  # 无 _NNN 序号 → 忽略
    (tmp_path / "loose.ts").write_bytes(b"x")  # 层级不对 → 忽略

    sessions = live_ingest.scan_live_dir(tmp_path)
    assert len(sessions) == 1
    assert len(sessions[0].segments) == 1


def test_scan_empty_root_returns_empty(tmp_path: Path) -> None:
    assert live_ingest.scan_live_dir(tmp_path) == []


def test_scan_multiple_authors_and_dates(tmp_path: Path) -> None:
    d1 = tmp_path / "主播A" / "2026-09-17"
    d2 = tmp_path / "主播B" / "2026-09-18"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / "a_000.TS").write_bytes(b"x")
    (d2 / "b_000.ts").write_bytes(b"x")  # 小写扩展名同样命中

    sessions = live_ingest.scan_live_dir(tmp_path)
    assert [(s.author, s.date) for s in sessions] == [("主播A", "2026-09-17"), ("主播B", "2026-09-18")]


def _seg(index: int, mtime: float) -> live_ingest.LiveSegment:
    return live_ingest.LiveSegment(index=index, path=Path(f"/x/{index}.ts"), mtime=mtime)


def test_split_day_sessions_breaks_at_gap_over_grace() -> None:
    """同日午/晚两场（李一恩 2026-09-22 实录）：空洞 > grace 切成两场。"""
    sd = live_ingest.LiveSessionDir(
        author="MS4wLjABxxx",
        date="2026-09-22",
        segments=[
            _seg(202609221133170000, 1000.0),
            _seg(202609221147100000, 1060.0),
            _seg(202609222047540000, 1000.0 + 30161),  # 8.4h 后加播
            _seg(202609222053450000, 1000.0 + 30161 + 60),
        ],
    )
    parts = live_ingest._split_day_sessions(sd, grace_sec=900)
    assert len(parts) == 2
    assert [s.index for s in parts[0].segments] == [202609221133170000, 202609221147100000]
    assert [s.index for s in parts[1].segments] == [202609222047540000, 202609222053450000]


def test_split_day_sessions_single_session_no_gap() -> None:
    """连续分片（间隔 ≤ grace）不切分；空目录返回单元素（防御）。"""
    sd = live_ingest.LiveSessionDir(
        author="a",
        date="2026-09-22",
        segments=[_seg(1, 1000.0), _seg(2, 1060.0), _seg(3, 1120.0)],
    )
    parts = live_ingest._split_day_sessions(sd, grace_sec=900)
    assert len(parts) == 1 and len(parts[0].segments) == 3
    assert live_ingest._split_day_sessions(
        live_ingest.LiveSessionDir(author="a", date="d", segments=[]), 900
    ) == []


def test_first_hhmm_extracts_time_only_from_timestamped_index() -> None:
    """合成序号（base 时间戳*1e4+序号）→ HH:MM；裸 _NNN 小序号无时间语义返回空。"""
    assert live_ingest._first_hhmm(202609222047540000) == "20:47"
    assert live_ingest._first_hhmm(202609221133170001) == "11:33"
    assert live_ingest._first_hhmm(3) == ""


def test_index_start_utc_parses_local_timestamp_to_utc() -> None:
    """合成序号前 14 位按本机时区解析转 UTC；裸序号/非法日期返回 None。"""
    from datetime import UTC, datetime

    start = live_ingest._index_start_utc(202609172222020000)
    assert start is not None
    local_tz = datetime.now().astimezone().tzinfo
    expected_local = datetime(2026, 9, 17, 22, 22, 2, tzinfo=local_tz)
    assert start == expected_local.astimezone(UTC)
    assert live_ingest._index_start_utc(3) is None
    assert live_ingest._index_start_utc(None) is None
    assert live_ingest._index_start_utc(202613172222020000) is None  # 13 月非法


class _FakeItem:
    """只带 _rename_if_stale 触碰的字段，避免拖起 DB。"""

    def __init__(self, metadata_json, title, published_at=None, duration_ms=None):
        self.metadata_json = metadata_json
        self.title = title
        self.published_at = published_at
        self.duration_ms = duration_ms


class _FakeAccount:
    id = 1
    external_id = "sec1"


def test_rename_if_stale_sets_published_at_and_duration(monkeypatch) -> None:
    """标题/起播时刻/时长都从注册表事实刷新（用户 2026-09-23 指定语义）。"""
    item = _FakeItem(
        {"live": {"processed": {
            "202609172222020000": {"duration_ms": 300000},
            "202609172226380000": {"duration_ms": 310000},
        }}},
        title="旧标题",
    )
    monkeypatch.setattr(
        live_ingest, "_session_title", lambda session, account, sd, *, first_index: (
            f"全能的野人 直播 2026-09-17 {live_ingest._first_hhmm(first_index)}"
        )
    )
    sd = live_ingest.LiveSessionDir(author="全能的野人", date="2026-09-17", segments=[])
    live_ingest._rename_if_stale(None, item, _FakeAccount(), sd, first_index=999)
    assert item.title == "全能的野人 直播 2026-09-17 22:22"
    assert item.duration_ms == 610000  # 分片时长加总
    assert item.published_at == live_ingest._index_start_utc(202609172222020000)
