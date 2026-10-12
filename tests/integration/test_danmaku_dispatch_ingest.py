"""弹幕派发器与 ingest 集成测试（Plan #5 Task 4）：候选查询/mtime 软闸/容量/幂等入库。"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from app.services.danmaku import dispatch as danmaku_dispatch
from app.services.danmaku import ingest as danmaku_ingest


def _settings(tmp_path: Path, **over):
    base = {
        "danmaku_ws_base_url": "ws://douyinlive.test:1088",
        "danmaku_sink_dir": str(tmp_path / "sink"),
        "danmaku_heartbeat_stale_sec": 120,
        "danmaku_max_collectors": 4,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _make_account_item(
    session,
    *,
    external_id,
    room_url="https://live.douyin.com/2040437791",
    live_monitor_enabled=True,
    status="transcribing",
    item_type="live",
):
    from app.db.models import Creator, SourceAccount, SourceItem

    creator = Creator(display_name=f"creator_{external_id}", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=external_id,
        live_room_url=room_url,
        discovery_mode="manual",
        poll_interval_sec=3600,
        live_monitor_enabled=live_monitor_enabled,
    )
    session.add(account)
    session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"live:{external_id}:2026-09-18",
        item_type=item_type,
        title="直播会话",
        status=status,
    )
    session.add(item)
    session.commit()
    return account, item


CHAT_ENV = json.dumps(
    {
        "v": 1,
        "received_at": "2026-09-18T12:00:00+00:00",
        "msg": {
            "method": "WebcastChatMessage",
            "common": {"msgId": "9001", "createTime": "1726660425000"},
            "user": {"id": "u1", "nickname": "小散一枚"},
            "content": "主播怎么看明天的大盘？",
        },
    },
    ensure_ascii=False,
)


# --- dispatch ---


def test_dispatch_unconfigured_noop(tmp_path):
    from unittest.mock import Mock

    out = danmaku_dispatch.dispatch_danmaku_collectors(
        Mock(), settings=_settings(tmp_path, danmaku_ws_base_url="")
    )
    assert "noop" in out


def test_dispatch_sends_for_stale_and_skips_fresh(migrated_db, db_session, tmp_path):
    _, item = _make_account_item(db_session, external_id="MS4wLjABdp1")
    sent: list[tuple] = []
    s = _settings(tmp_path)

    first = danmaku_dispatch.dispatch_danmaku_collectors(
        db_session, settings=s, sender=lambda task, args: sent.append((task, args))
    )
    assert first == {"candidates": 1, "active": 0, "dispatched": 1, "no_room": 0}
    assert sent == [("collect_danmaku", [item.id, "2040437791"])]

    # 心跳新鲜（mtime 刚 touch）→ 视为采集器在跑，不重复派发（F2 软闸）
    sink = tmp_path / "sink" / "2026-09-18" / f"{item.id}.jsonl"
    sink.parent.mkdir(parents=True)
    sink.write_text(CHAT_ENV + "\n")
    second = danmaku_dispatch.dispatch_danmaku_collectors(
        db_session, settings=s, sender=lambda task, args: sent.append((task, args))
    )
    assert second["active"] == 1 and second["dispatched"] == 0
    assert len(sent) == 1

    # 心跳过期（mtime 回拨）→ 重新派发
    old = time.time() - 999
    os.utime(sink, (old, old))
    third = danmaku_dispatch.dispatch_danmaku_collectors(
        db_session, settings=s, sender=lambda task, args: sent.append((task, args))
    )
    assert third["dispatched"] == 1


def test_dispatch_skips_without_room_id_and_respects_capacity(
    migrated_db, db_session, tmp_path
):
    # 无房间号（只有主页 URL）→ no_room
    _make_account_item(
        db_session, external_id="MS4wLjABnord", room_url="https://www.douyin.com/user/x"
    )
    # 候选 2：房间正常
    _make_account_item(db_session, external_id="MS4wLjABcap")
    sent: list[tuple] = []
    s = _settings(tmp_path, danmaku_max_collectors=0)  # 容量为 0 → 不派发
    out = danmaku_dispatch.dispatch_danmaku_collectors(
        db_session, settings=s, sender=lambda task, args: sent.append((task, args))
    )
    assert out["no_room"] == 1
    assert out["dispatched"] == 0 and sent == []


def test_dispatch_ignores_non_douyin_or_disabled(migrated_db, db_session, tmp_path):
    _make_account_item(db_session, external_id="MS4wLjABoff", live_monitor_enabled=False)
    _make_account_item(db_session, external_id="MS4wLjABdone", status="transcribed")
    sent: list[tuple] = []
    out = danmaku_dispatch.dispatch_danmaku_collectors(
        db_session,
        settings=_settings(tmp_path),
        sender=lambda task, args: sent.append((task, args)),
    )
    assert out["candidates"] == 0


# --- ingest ---


def _write_sink(tmp_path: Path, item_id: int, lines: list[str]) -> Path:
    sink = tmp_path / "sink" / "2026-09-18" / f"{item_id}.jsonl"
    sink.parent.mkdir(parents=True, exist_ok=True)
    sink.write_text("\n".join(lines) + "\n")
    return sink


def test_ingest_parses_dedupes_and_counts(migrated_db, db_session, tmp_path):
    from app.db.models import LiveChatMessage

    _, item = _make_account_item(db_session, external_id="MS4wLjABing1")
    member_env = CHAT_ENV.replace('"msgId": "9001"', '"msgId": "9002"').replace(
        "WebcastChatMessage", "WebcastMemberMessage"
    )
    _write_sink(
        tmp_path,
        item.id,
        [
            CHAT_ENV,
            CHAT_ENV,  # 文件内重复 → 批内去重
            member_env,
            json.dumps({"v": 1, "msg": {"method": "WebcastRoomUserSeqMessage"}}),  # 不支持类型
            "not-json",  # 坏行
            '{"v":1,"rece',  # 半行
        ],
    )
    out = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert out["files"] == 1
    # 产品裁决（2026-09-19）：只入库弹幕（chat）；member/不支持类型/坏行/半行全部 skip
    assert out["messages_inserted"] == 1
    assert out["messages_skipped"] == 4

    rows = db_session.query(LiveChatMessage).order_by(LiveChatMessage.id).all()
    assert [r.external_msg_id for r in rows] == ["9001"]
    assert rows[0].text == "主播怎么看明天的大盘？"

    # 重放幂等（F5）：同文件再扫一遍零新增
    again = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert again["messages_inserted"] == 0
    assert db_session.query(LiveChatMessage).count() == 1


def test_ingest_skips_file_without_item(migrated_db, db_session, tmp_path):
    _write_sink(tmp_path, 987654, [CHAT_ENV])
    out = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert out["files_skipped"] == 1 and out["files"] == 0


def test_ingest_ignores_foreign_files(migrated_db, db_session, tmp_path):
    _, item = _make_account_item(db_session, external_id="MS4wLjABing2")
    sink_root = tmp_path / "sink"
    (sink_root / "2026-09-18").mkdir(parents=True)
    (sink_root / "2026-09-18" / f"{item.id}.jsonl").write_text(CHAT_ENV + "\n")
    (sink_root / "2026-09-18" / "notes.jsonl").write_text(CHAT_ENV + "\n")  # 非 item id
    (sink_root / "2026-9-18" ).mkdir()
    (sink_root / "2026-9-18" / f"{item.id}.jsonl").write_text(CHAT_ENV + "\n")  # 日期段不规范
    out = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert out["files"] == 1


def test_ingest_noop_without_config(db_session):
    out = danmaku_ingest.ingest_danmaku_files(
        db_session, settings=_settings(Path("/tmp"), danmaku_sink_dir="")
    )
    assert "noop" in out


def test_ingest_watermark_incremental_and_partial_tail(migrated_db, db_session, tmp_path):
    """字节水位线（2026-10-12）：二轮只读增量；无换行的半行留待下轮。"""
    from app.db.models import LiveChatMessage

    _, item = _make_account_item(db_session, external_id="MS4wLjABwm1")
    sink = _write_sink(tmp_path, item.id, [CHAT_ENV])

    first = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert first["messages_inserted"] == 1
    assert first["files_unchanged"] == 0
    pos_file = sink.with_name(sink.name + ".pos")
    assert pos_file.exists() and int(pos_file.read_text()) == sink.stat().st_size

    # 未变化 → 零解析快路径
    second = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert second["files_unchanged"] == 1 and second["messages_inserted"] == 0

    # 追加一条完整 + 一条无换行半行：半行不入库，水位线停在其前
    other = CHAT_ENV.replace('"msgId": "9001"', '"msgId": "9003"')
    with sink.open("a") as fh:
        fh.write(other + "\n")
        fh.write('{"v":1,"rece')
    third = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert third["messages_inserted"] == 1
    assert int(pos_file.read_text()) == sink.stat().st_size - len('{"v":1,"rece')

    # 半行补完（补换行）→ 下轮读走
    with sink.open("a") as fh:
        fh.write('ived_at":"2026-10-12T00:00:00+00:00"}\n')
    fourth = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert fourth["messages_skipped"] == 1  # 坏 JSON 但被完整读到
    assert int(pos_file.read_text()) == sink.stat().st_size
    assert db_session.query(LiveChatMessage).count() == 2


def test_ingest_watermark_resets_on_truncation(migrated_db, db_session, tmp_path):
    """文件被截断（size < pos）→ 水位线归零重读，幂等靠唯一约束。"""
    _, item = _make_account_item(db_session, external_id="MS4wLjABwm2")
    sink = _write_sink(tmp_path, item.id, [CHAT_ENV])
    danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    pos_file = sink.with_name(sink.name + ".pos")

    sink.write_text(CHAT_ENV + "\n")  # 重写（比 pos 短不了多少但触发 size<pos 分支需更小）
    pos_file.write_text(str(sink.stat().st_size + 500))  # 人为超前水位线
    out = danmaku_ingest.ingest_danmaku_files(db_session, settings=_settings(tmp_path))
    assert out["messages_inserted"] == 0  # 重读但去重零新增（F5 幂等）
    assert int(pos_file.read_text()) == sink.stat().st_size


def test_housekeeping_prunes_ops_rows(migrated_db, db_session, monkeypatch, tmp_path):
    """运维流水保留：job_run/api_call_log 超 30 天删除，新行保留。"""
    from datetime import UTC, datetime, timedelta

    from app.db.models import ApiCallLog, JobRun
    from app.worker import tasks as tasks_mod
    from sqlalchemy.orm import sessionmaker

    # 任务模块级导入了 get_session_factory，需补丁在 tasks 命名空间上
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(tasks_mod, "get_session_factory", lambda: factory)

    old = datetime.now(UTC) - timedelta(days=40)
    new = datetime.now(UTC) - timedelta(days=1)
    db_session.add_all([
        JobRun(job_type="prepare", status="success", created_at=old),
        JobRun(job_type="extract", status="success", created_at=new),
        ApiCallLog(api_key_id=None, method="GET", path="/open/v1/x",
                   status_code=200, created_at=old),
        ApiCallLog(api_key_id=None, method="GET", path="/open/v1/y",
                   status_code=200, created_at=new),
    ])
    db_session.commit()

    out = tasks_mod.housekeeping_stale_jobs()

    assert out["job_run_pruned"] == 1 and out["api_call_log_pruned"] == 1
    assert db_session.query(JobRun).count() == 1
    assert db_session.query(ApiCallLog).count() == 1
