"""弹幕 jsonl 档案保留清理单测（2026-10-12：磁盘慢泄漏修复）。

不依赖 DB（纯文件系统），与 test_disk_retention 的 live_segments 用例互补。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.services.live_retention import sweep_danmaku_sinks


def _settings(tmp_path: Path, days: int = 7) -> SimpleNamespace:
    return SimpleNamespace(danmaku_sink_dir=str(tmp_path), danmaku_sink_retention_days=days)


def _mk(tmp_path: Path, date: str, item: int, *, age_days: float = 0) -> Path:
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{item}.jsonl"
    f.write_text('{"v":1}\n')
    stamp = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    os.utime(f, (stamp, stamp))
    return f


def test_deletes_expired_files_and_empty_dirs(tmp_path: Path) -> None:
    _mk(tmp_path, "2026-10-01", 100, age_days=10)
    _mk(tmp_path, "2026-10-01", 101, age_days=10)
    keep = _mk(tmp_path, "2026-10-11", 200, age_days=1)

    result = sweep_danmaku_sinks(_settings(tmp_path))

    assert result["files_deleted"] == 2
    assert not (tmp_path / "2026-10-01").exists()  # 空日期目录连根清
    assert keep.exists()


def test_boundary_file_at_cutoff_kept(tmp_path: Path) -> None:
    """恰好到期当天的文件不删（cutoff 严格小于语义，与分片清理一致）。"""
    _mk(tmp_path, "2026-10-05", 1, age_days=6)
    result = sweep_danmaku_sinks(_settings(tmp_path, days=7))
    assert result["files_deleted"] == 0


def test_zero_days_disables(tmp_path: Path) -> None:
    _mk(tmp_path, "2026-10-01", 1, age_days=30)
    assert sweep_danmaku_sinks(_settings(tmp_path, days=0)) == {"noop": True}
    assert (tmp_path / "2026-10-01" / "1.jsonl").exists()


def test_missing_dir_noop(tmp_path: Path) -> None:
    assert sweep_danmaku_sinks(
        SimpleNamespace(danmaku_sink_dir=str(tmp_path / "nope"), danmaku_sink_retention_days=7)
    ) == {"noop": True}


def test_non_jsonl_files_untouched(tmp_path: Path) -> None:
    other = tmp_path / "2026-10-01" / "notes.txt"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    stamp = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(other, (stamp, stamp))
    _mk(tmp_path, "2026-10-01", 1, age_days=30)

    sweep_danmaku_sinks(_settings(tmp_path))

    assert other.exists()  # 只清 *.jsonl
    assert not (tmp_path / "2026-10-01" / "1.jsonl").exists()
