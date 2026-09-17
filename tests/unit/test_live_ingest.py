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
