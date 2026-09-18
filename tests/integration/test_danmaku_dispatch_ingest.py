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
