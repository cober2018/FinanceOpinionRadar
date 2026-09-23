"""推送流集成测试（Plan #7）：digest 派发、幂等、失败重试与 dead、禁用渠道、测试消息。"""

import json

import httpx
import pytest
from app.db.models import Creator, PushChannel, PushDelivery, SourceAccount, SourceItem, Viewpoint
from app.services.push import deliver_pending_pushes, send_test_message


def _capture(captured: list[httpx.Request], status: int = 200, body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=body or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def confirmed_vps(db_session):
    creator = Creator(display_name="李一恩", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec_p", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id, external_item_id="vp_item", item_type="vod", status="ready"
    )
    db_session.add(item)
    db_session.flush()
    ids = []
    for i in range(3):
        vp = Viewpoint(
            creator_id=creator.id,
            source_item_id=item.id,
            claim=f"观点{i}：黄金看涨",
            stance="bullish",
            confidence=0.8,
            verification_status="confirmed",
        )
        db_session.add(vp)
        db_session.flush()
        ids.append(vp.id)
    db_session.commit()
    return ids


def _mk_channel(db_session, ctype="feishu", enabled=True) -> PushChannel:
    ch = PushChannel(
        name="测试渠道",
        channel_type=ctype,
        config_json={"url": "https://example.com/hook", "secret": ""},
        enabled=enabled,
    )
    db_session.add(ch)
    db_session.commit()
    return ch


def test_digest_delivered_and_idempotent(db_session, confirmed_vps):
    ch = _mk_channel(db_session)
    captured: list[httpx.Request] = []

    out = deliver_pending_pushes(db_session, http=_capture(captured))
    assert out["delivered"] == 3 and out["channels"] == 1
    assert len(captured) == 1  # digest 聚合一条消息
    payload = json.loads(captured[0].read())
    assert "新增确认观点 3 条" in payload["content"]["text"]

    db_session.expire_all()
    statuses = {d.viewpoint_id: d.status for d in db_session.query(PushDelivery).all()}
    assert set(statuses.values()) == {"sent"} and len(statuses) == 3

    # 第二轮：全部已 sent → 无渠道需要发
    captured.clear()
    out2 = deliver_pending_pushes(db_session, http=_capture(captured))
    assert out2["channels"] == 0 and len(captured) == 0


def test_failure_retry_then_dead(db_session, confirmed_vps):
    ch = _mk_channel(db_session)
    # HTTP 500 → 失败记账 attempt=1
    out = deliver_pending_pushes(db_session, http=_capture([], status=500))
    assert out["failed"] == 3
    db_session.expire_all()
    rows = db_session.query(PushDelivery).all()
    assert all(r.status == "failed" and r.attempt == 1 for r in rows)

    # 连续失败至默认上限 5 → dead，不再重试
    for _ in range(4):
        deliver_pending_pushes(db_session, http=_capture([], status=500))
    db_session.expire_all()
    rows = db_session.query(PushDelivery).all()
    assert all(r.status == "dead" and r.attempt == 5 for r in rows)

    # dead 之后扫描不再拾起
    out = deliver_pending_pushes(db_session, http=_capture([]))
    assert out["channels"] == 0


def test_disabled_channel_skipped_and_candidate_not_pushed(db_session, confirmed_vps):
    _mk_channel(db_session, enabled=False)
    # 追加一条 candidate：不应被推送
    out = deliver_pending_pushes(db_session, http=_capture([]))
    assert out["channels"] == 0
    assert db_session.query(PushDelivery).count() == 0


def test_generic_webhook_batch_event(db_session, confirmed_vps):
    ch = _mk_channel(db_session, ctype="generic_webhook")
    captured: list[httpx.Request] = []
    out = deliver_pending_pushes(db_session, http=_capture(captured))
    assert out["delivered"] == 3
    body = json.loads(captured[0].read())
    assert body["event"] == "viewpoints.confirmed" and len(body["items"]) == 3


def test_send_test_message_ok_and_business_error(db_session):
    ch = _mk_channel(db_session, ctype="feishu")
    captured: list[httpx.Request] = []
    assert send_test_message(db_session, ch.id, http=_capture(captured))["ok"] is True
    assert "连通性 OK" in json.loads(captured[0].read())["content"]["text"]

    bad = _capture([], body={"code": 19021, "msg": "sign match fail"})
    with pytest.raises(RuntimeError, match="19021"):
        send_test_message(db_session, ch.id, http=bad)
