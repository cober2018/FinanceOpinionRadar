"""弹幕采集器集成测试（Plan #5 Task 3）：真 PG advisory lock + fake WS（零网络）。

采集行为契约（F2/F4/F6）：单飞闸、会话收尾退出、ROOM_NOT_FOUND 退出、断线重连、
寿命上限、jsonl envelope 落盘。纯解析逻辑在 tests/unit/test_danmaku_parse.py。
"""

import json
from pathlib import Path

import pytest
from app.db.models import Creator, LiveChatMessage, SourceAccount, SourceItem
from app.services.danmaku import collector
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from websocket import WebSocketException, WebSocketTimeoutException


class FakeWS:
    """脚本化 fake：recv 依序消费 script；耗尽后恒抛 WS 超时。每次超时回调 on_timeout。"""

    def __init__(self, script, on_timeout=None):
        self.script = list(script)
        self.on_timeout = on_timeout
        self.sent: list[str] = []
        self.closed = False

    def settimeout(self, t):
        self.timeout_setting = t

    def send(self, msg):
        self.sent.append(msg)

    def recv(self):
        if not self.script:
            self._timeout()
            raise WebSocketTimeoutException()
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item == "TO":
            self._timeout()
            raise WebSocketTimeoutException()
        return item

    def _timeout(self):
        if self.on_timeout:
            self.on_timeout()

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _make_session_factory(url: str):
    engine = create_engine(url)
    factory = sessionmaker(bind=engine)
    return lambda: factory()


def _make_item(session, *, external_id="MS4wLjABdanmaku1", status="transcribing") -> SourceItem:
    creator = Creator(display_name=f"creator_{external_id}", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=external_id,
        live_room_url="https://live.douyin.com/2040437791",
        discovery_mode="manual",
        poll_interval_sec=3600,
        live_monitor_enabled=True,
    )
    session.add(account)
    session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"live:{external_id}:2026-09-18",
        item_type="live",
        title="大潘说股 直播 2026-09-18",
        status=status,
    )
    session.add(item)
    session.commit()
    return item


def _flip_status(url: str, item_id: int, status: str) -> None:
    """模拟 live_ingest 收尾（另一连接写状态；采集器零 DB 写，仅周期读）。"""
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE source_item SET status = :s WHERE id = :i"),
            {"s": status, "i": item_id},
        )
    engine.dispose()


SYS_ONLINE = json.dumps(
    {
        "type": "system",
        "event": "live_status",
        "code": "ROOM_ONLINE",
        "live": True,
        "room_id": "2040437791",
    },
    ensure_ascii=False,
)
SYS_NOT_FOUND = json.dumps(
    {"type": "system", "event": "live_status", "code": "ROOM_NOT_FOUND", "valid": False}
)
CHAT = json.dumps(
    {
        "method": "WebcastChatMessage",
        "common": {"msgId": "9001", "createTime": "1726660425000"},
        "user": {"id": "u1", "nickname": "小散一枚"},
        "content": "主播怎么看明天的大盘？",
    },
    ensure_ascii=False,
)


def _settings(tmp_path: Path, **over):
    from types import SimpleNamespace

    base = {
        "danmaku_ws_base_url": "ws://douyinlive.test:1088",
        "danmaku_sink_dir": str(tmp_path / "sink"),
        "danmaku_collector_max_duration_sec": 43200,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_collect_unconfigured_is_noop(tmp_path):
    out = collector.collect_danmaku(
        1, "123", settings=_settings(tmp_path, danmaku_ws_base_url="")
    )
    assert "noop" in out


def test_collect_writes_envelopes_then_exits_on_session_close(
    migrated_db, db_session, tmp_path
):
    item = _make_item(db_session)
    s = _settings(tmp_path)
    clock = FakeClock()
    factory = _make_session_factory(migrated_db)
    ticks = [0]

    def flip_on_first_housekeeping():
        ticks[0] += 1
        if ticks[0] >= 6:  # housekeeping 节拍 = 6 次 recv 超时
            _flip_status(migrated_db, item.id, "transcribed")

    ws = FakeWS([SYS_ONLINE, CHAT, "TO"], on_timeout=flip_on_first_housekeeping)
    out = collector.collect_danmaku(
        item.id,
        "2040437791",
        ws_factory=lambda url: ws,
        session_factory=factory,
        settings=s,
        sleep=lambda sec: setattr(clock, "t", clock.t + 1),
        clock=clock,
    )
    assert out["exit_reason"] == "session_closed"
    assert out["collected"] == 2
    assert out["connects"] == 1
    assert "ping" in ws.sent  # 保活文本 ping（30s 节拍）
    # envelope 落盘：<sink>/<日期段>/<item_id>.jsonl
    sink = tmp_path / "sink" / "2026-09-18" / f"{item.id}.jsonl"
    lines = [json.loads(x) for x in sink.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["v"] == 1 and lines[0]["msg"]["code"] == "ROOM_ONLINE"
    assert lines[1]["msg"]["method"] == "WebcastChatMessage"
    assert lines[1]["msg"]["common"]["msgId"] == "9001"
    assert "received_at" in lines[0]


def test_collect_singleton_lock_denied(migrated_db, db_session, tmp_path):
    item = _make_item(db_session)
    s = _settings(tmp_path)
    holder = create_engine(migrated_db)
    conn = holder.connect()
    conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": collector._lock_key(item.id)})
    conn.commit()
    try:
        out = collector.collect_danmaku(
            item.id,
            "2040437791",
            ws_factory=lambda url: pytest.fail("不应建立连接"),
            session_factory=_make_session_factory(migrated_db),
            settings=s,
            sleep=lambda sec: None,
        )
        assert out == {"skipped": "singleton"}
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": collector._lock_key(item.id)})
        conn.commit()
        conn.close()
        holder.dispose()


def test_collect_room_not_found_exits_without_reconnect(migrated_db, db_session, tmp_path):
    item = _make_item(db_session)
    s = _settings(tmp_path)
    connects = []

    def factory(url):
        connects.append(url)
        return FakeWS([SYS_NOT_FOUND])

    out = collector.collect_danmaku(
        item.id,
        "999",
        ws_factory=factory,
        session_factory=_make_session_factory(migrated_db),
        settings=s,
        sleep=lambda sec: None,
    )
    # NOT_FOUND 消息本身先归档再退出（F3 原始档案完整性）
    assert out == {"collected": 1, "connects": 1, "exit_reason": "room_not_found"}
    assert connects == ["ws://douyinlive.test:1088/ws/999"]


def test_collect_reconnects_after_disconnect_until_session_close(
    migrated_db, db_session, tmp_path
):
    item = _make_item(db_session)
    s = _settings(tmp_path)
    clock = FakeClock()
    fakes = [
        FakeWS([WebSocketException("boom")]),  # 第一条连接：立即断开 → 重连
        FakeWS([CHAT, "TO"]),
    ]
    ticks = [0]

    def flip_on_first_housekeeping():
        ticks[0] += 1
        if ticks[0] >= 6:
            _flip_status(migrated_db, item.id, "transcribed")

    fakes[1].on_timeout = flip_on_first_housekeeping
    out = collector.collect_danmaku(
        item.id,
        "2040437791",
        ws_factory=lambda url: fakes.pop(0),
        session_factory=_make_session_factory(migrated_db),
        settings=s,
        sleep=lambda sec: setattr(clock, "t", clock.t + 1),
        clock=clock,
    )
    assert out["connects"] == 2
    assert out["exit_reason"] == "session_closed"
    assert out["collected"] == 1


def test_collect_max_duration_exit(migrated_db, db_session, tmp_path):
    item = _make_item(db_session)
    s = _settings(tmp_path, danmaku_collector_max_duration_sec=100)
    clock = FakeClock()
    ticks = [0]

    def blow_past_deadline():
        ticks[0] += 1
        if ticks[0] >= 6:
            clock.t += 10_000  # housekeeping 时越过寿命上限

    ws = FakeWS(["TO"], on_timeout=blow_past_deadline)
    out = collector.collect_danmaku(
        item.id,
        "2040437791",
        ws_factory=lambda url: ws,
        session_factory=_make_session_factory(migrated_db),
        settings=s,
        sleep=lambda sec: setattr(clock, "t", clock.t + 1),
        clock=clock,
    )
    assert out["exit_reason"] == "max_duration"


def test_collect_item_missing(migrated_db, db_session, tmp_path):
    out = collector.collect_danmaku(
        424242,
        "1",
        ws_factory=lambda url: pytest.fail("不应建立连接"),
        session_factory=_make_session_factory(migrated_db),
        settings=_settings(tmp_path),
        sleep=lambda sec: None,
    )
    assert out == {"skipped": "item_missing"}


def test_envelope_keeps_raw_for_bad_json():
    env = json.loads(collector.build_envelope("not-json{{{"))
    assert env["v"] == 1 and env["raw"] == "not-json{{{"
    assert "msg" not in env


def test_sink_rows_parse_and_dedupe(migrated_db, db_session, tmp_path):
    """端到端微缩：collector 落盘 → ingest 入库 → 重放不重复（F5）。"""
    from app.services.danmaku import ingest as danmaku_ingest

    item = _make_item(db_session)
    sink = tmp_path / "sink" / "2026-09-18" / f"{item.id}.jsonl"
    envelope = collector.build_envelope(CHAT)
    sink.parent.mkdir(parents=True)
    sink.write_text(envelope + "\n" + envelope + "\n")

    db_session.execute(
        text("UPDATE source_item SET status = 'transcribed' WHERE id = :i"), {"i": item.id}
    )
    db_session.commit()

    s = _settings(tmp_path)
    first = danmaku_ingest.ingest_danmaku_files(db_session, settings=s)
    assert first["messages_inserted"] == 1
    second = danmaku_ingest.ingest_danmaku_files(db_session, settings=s)
    assert second["messages_inserted"] == 0
    assert db_session.query(LiveChatMessage).count() == 1


def test_collect_busy_room_housekeeping_on_wall_clock(migrated_db, db_session, tmp_path):
    """忙房间回归（真栈实录：西楚老温房间，2026-09-19）：消息流恒不断 → recv 永不超时，
    housekeeping 必须按壁钟触发——修复前 ping 缺失 + 会话收尾观察不到 = 采集器永不退出。"""
    item = _make_item(db_session)
    s = _settings(tmp_path)
    clock = FakeClock()

    class BusyWS:
        def __init__(self):
            self.sent: list[str] = []
            self.closed = False
            self.calls = 0

        def settimeout(self, t):
            pass

        def send(self, msg):
            self.sent.append(msg)

        def recv(self):
            self.calls += 1
            clock.t += 10  # 每条消息 10s 虚拟间隔；恒有消息 = recv 永不超时
            if self.calls >= 3:  # housekeeping 观察前先翻转状态（模拟 live_ingest 收尾）
                _flip_status(migrated_db, item.id, "transcribed")
            return CHAT

        def close(self):
            self.closed = True

    ws = BusyWS()
    out = collector.collect_danmaku(
        item.id,
        "2040437791",
        ws_factory=lambda url: ws,
        session_factory=_make_session_factory(migrated_db),
        settings=s,
        sleep=lambda sec: setattr(clock, "t", clock.t + 1),
        clock=clock,
    )
    assert out["exit_reason"] == "session_closed"
    assert out["connects"] == 1
    # 第 3 条消息触发壁钟 housekeeping：观察到收尾即退出（退出优先于该消息落盘）
    assert out["collected"] == 2
    assert "ping" in ws.sent  # 忙房间也必须有 ping（否则被服务端断连）
