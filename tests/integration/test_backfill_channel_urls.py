"""注记③ 存量补扫迁移：仅 YouTube 裸频道地址追加 /videos，幂等且不动其他平台。"""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration.test_migrations import _cfg

_PRE_BACKFILL_REV = "274c026452c4"  # backfill 的 down_revision


def _insert_account(conn, external_id: str, platform: str, url: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO source_account"
            " (creator_id, platform, external_id, url, discovery_mode, poll_interval_sec,"
            "  enabled, failure_count)"
            " VALUES ((SELECT min(id) FROM creator), :p, :e, :u, 'manual', 3600, true, 0)"
        ),
        {"p": platform, "e": external_id, "u": url},
    )


def test_backfill_appends_videos_only_for_bare_youtube(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    cfg = _cfg(migrated_db)
    # 回到补扫前一版，准备脏数据（creator 已由 seed/迁移前状态保证至少无行，自建一行）
    command.downgrade(cfg, _PRE_BACKFILL_REV)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO creator (display_name, status) VALUES ('c', 'active')")
        )
        _insert_account(conn, "bare", "youtube", "https://www.youtube.com/@macro-diary")
        _insert_account(
            conn, "hastab", "youtube", "https://www.youtube.com/@macro-diary/videos"
        )
        _insert_account(conn, "douyin", "douyin", "https://www.douyin.com/user/xyz")
        _insert_account(conn, "nourl", "youtube", None)

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        got = dict(conn.execute(text("SELECT external_id, url FROM source_account")).all())
    assert got["bare"] == "https://www.youtube.com/@macro-diary/videos"
    assert got["hastab"] == "https://www.youtube.com/@macro-diary/videos"
    assert got["douyin"] == "https://www.douyin.com/user/xyz"
    assert got["nourl"] is None

    # 幂等：alembic 版本已推进，重复 upgrade 不再改数据
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        got2 = dict(conn.execute(text("SELECT external_id, url FROM source_account")).all())
    assert got2 == got
    engine.dispose()
