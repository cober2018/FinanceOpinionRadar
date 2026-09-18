"""观众互动 API 集成测试（2026-09-19）：/danmaku/stats + 弹幕详情 + 列表 chat_count。"""

import pytest
from app.db.models import (
    ContentSummary,
    Creator,
    LiveChatMessage,
    SourceAccount,
    SourceItem,
)
from app.db.session import get_db
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _make_session_with_chats(db_session, *, external_id="MS4wLjABdmapi1", chats=True):
    creator = Creator(display_name=f"creator_{external_id}", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=external_id,
        live_room_url="https://live.douyin.com/2040437791",
        discovery_mode="manual",
        poll_interval_sec=3600,
        live_monitor_enabled=True,
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"live:{external_id}:2026-09-19",
        item_type="live",
        title="西楚老温 直播 2026-09-19",
        status="transcribed",
    )
    db_session.add(item)
    db_session.flush()
    for i, (user, text) in enumerate(
        [("心***", "消费"), ("年***", "创新药"), ("晨***", "PCB是不是还要回落调整了")]
        if chats
        else []
    ):
        db_session.add(
            LiveChatMessage(
                source_item_id=item.id,
                platform="douyin",
                msg_type="WebcastChatMessage",
                external_msg_id=f"m{i}",
                user_id="1111111111111111111",
                user_name=user,
                text=text,
            )
        )
    db_session.commit()
    return item


def test_danmaku_stats(db_session, client):
    _make_session_with_chats(db_session)
    resp = client.get("/api/v1/danmaku/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 3
    assert body["sessions"] >= 1
    assert body["latest_message_at"] is None  # 测试行未填 published_at


def test_chat_messages_detail(db_session, client):
    item = _make_session_with_chats(db_session)
    resp = client.get(f"/api/v1/source-items/{item.id}/chat-messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "西楚老温 直播 2026-09-19"
    assert body["total"] == 3
    assert [m["user_name"] for m in body["messages"]] == ["心***", "年***", "晨***"]
    assert body["messages"][2]["text"] == "PCB是不是还要回落调整了"


def test_chat_messages_404(client):
    resp = client.get("/api/v1/source-items/987654/chat-messages")
    assert resp.status_code == 404


def test_chat_messages_truncated_to_first_10(db_session, client):
    """用户裁决（2026-09-19）：抽屉只显示前 10 条，total 仍为全量计数。"""
    item = _make_session_with_chats(db_session)
    for i in range(12):
        db_session.add(
            LiveChatMessage(
                source_item_id=item.id,
                platform="douyin",
                msg_type="WebcastChatMessage",
                external_msg_id=f"extra{i}",
                user_name=f"用户{i}",
                text=f"消息{i}",
            )
        )
    db_session.commit()
    resp = client.get(f"/api/v1/source-items/{item.id}/chat-messages")
    body = resp.json()
    assert body["total"] == 15  # 3 + 12
    assert len(body["messages"]) == 10  # 只返回前 10 条
    assert body["messages"][0]["text"] == "消费"  # 最早的在前


def test_library_list_includes_chat_count(db_session, client):
    item = _make_session_with_chats(db_session)
    _make_session_with_chats(db_session, external_id="MS4wLjABdmapi2", chats=False)  # 无弹幕对照
    resp = client.get("/api/v1/source-items")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 2
    row = next(r for r in body if r["id"] == item.id)
    assert row["chat_count"] == 3
    other = next(r for r in body if r["id"] != item.id)
    assert other["chat_count"] == 0


# --- Plan #6：精华资产与 retention API ---


def test_toggle_asset_marks_and_unmarks(db_session, client):
    item = _make_session_with_chats(db_session)
    resp = client.post(f"/api/v1/source-items/{item.id}/asset")
    assert resp.status_code == 200 and resp.json()["is_asset"] is True
    assert db_session.get(SourceItem, item.id).asset_at is not None
    # 幂等显式取消
    resp = client.post(f"/api/v1/source-items/{item.id}/asset?is_asset=false")
    assert resp.json()["is_asset"] is False
    assert db_session.get(SourceItem, item.id).asset_at is None


def test_toggle_asset_404(client):
    assert client.post("/api/v1/source-items/987654/asset").status_code == 404


def test_list_filters_by_asset(db_session, client):
    item = _make_session_with_chats(db_session)
    other = _make_session_with_chats(db_session, external_id="MS4wLjABdmapi3", chats=False)
    client.post(f"/api/v1/source-items/{item.id}/asset?is_asset=true")
    resp = client.get("/api/v1/source-items?asset=true")
    ids = [r["id"] for r in resp.json()]
    assert item.id in ids and other.id not in ids
    row = next(r for r in resp.json() if r["id"] == item.id)
    assert row["is_asset"] is True and row["expires_at"] is None  # 精华永不过期


def test_retention_sweep_endpoint_dry_run(db_session, client):
    from datetime import UTC, datetime, timedelta

    from app.db.models import Creator, SourceAccount, SourceItem

    creator = Creator(display_name="c_retdry", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="MS4wLjABretdry",
        discovery_mode="manual", poll_interval_sec=3600,
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(
        SourceItem(
            source_account_id=account.id,
            external_item_id="vod:MS4wLjABretdry:2026-09-19",
            title="过期条目",
            status="transcribed",
            created_at=datetime.now(UTC) - timedelta(days=60),
        )
    )
    db_session.commit()
    resp = client.post("/api/v1/retention/sweep?dry_run=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["expired"] >= 1 and body["swept"] == 0
    resp = client.post("/api/v1/retention/sweep")
    assert resp.json()["swept"] >= 1
    assert db_session.query(ContentSummary).count() >= 1
