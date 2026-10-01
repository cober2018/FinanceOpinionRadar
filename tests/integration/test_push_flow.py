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


# --- 直播分片快报（2026-09-30：推送按分片粒度，系统内仍是整场一个视频）---


def _mk_live_confirmed(db_session, *, display_name="李一恩", closed=False):
    """进行中的直播条目 + 2 条已确认观点（带证据偏移 5min/12min）。"""
    from app.db.models import TranscriptSegment, ViewpointEvidence
    from datetime import UTC, datetime

    creator = Creator(display_name=display_name, status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec_live", discovery_mode="manual"
    )
    db_session.add(account)
    db_session.flush()
    start = datetime(2026, 9, 30, 19, 0, tzinfo=UTC)
    item = SourceItem(
        source_account_id=account.id,
        external_item_id="live_push_item",
        item_type="live",
        status="transcribing",
        published_at=start,
        metadata_json={"live": {"closed": closed}},
    )
    db_session.add(item)
    db_session.flush()
    vps = []
    for i, (offset_min, claim) in enumerate([(5, "黄金看涨"), (12, "大盘中性")]):
        seg = TranscriptSegment(
            source_item_id=item.id, sequence_no=i + 1,
            start_ms=offset_min * 60000, end_ms=(offset_min + 1) * 60000, text=claim,
        )
        db_session.add(seg)
        db_session.flush()
        vp = Viewpoint(
            creator_id=creator.id, source_item_id=item.id, claim=claim,
            stance="bullish" if i == 0 else "neutral", confidence=0.9,
            verification_status="confirmed",
        )
        db_session.add(vp)
        db_session.flush()
        db_session.add(
            ViewpointEvidence(
                viewpoint_id=vp.id, transcript_segment_id=seg.id,
                start_ms=seg.start_ms, end_ms=seg.end_ms, evidence_text=claim, evidence_order=1,
            )
        )
        vps.append(vp)
    db_session.commit()
    return item, vps


def test_live_ongoing_pushes_bulletin_with_clock_time(db_session, confirmed_vps):
    """进行中的直播观点 → 独立「直播快报」消息（带 HH:mm 钟点），与 vod digest 分开发送。"""
    item, live_vps = _mk_live_confirmed(db_session)
    ch = _mk_channel(db_session)
    captured: list[httpx.Request] = []

    out = deliver_pending_pushes(db_session, http=_capture(captured))
    assert out["delivered"] == 5  # 3 vod + 2 live
    assert len(captured) == 2  # 1 条 vod digest + 1 条直播快报
    texts = [json.loads(r.read())["content"]["text"] for r in captured]
    bulletin = next(t for t in texts if "直播快报" in t)
    digest = next(t for t in texts if "新增确认观点" in t)
    assert "李一恩 直播进行中" in bulletin
    # 钟点 = 起播 19:00 + 偏移 5/12 分钟（UTC；fmt 无时区转换，直接按存的时间）
    assert "19:05" in bulletin and "19:12" in bulletin
    assert "黄金看涨" in bulletin
    # vod 观点仍在 digest 里、不混入快报
    assert "观点0" in digest and "观点0" not in bulletin
    # 幂等：第二轮无消息
    captured.clear()
    out2 = deliver_pending_pushes(db_session, http=_capture(captured))
    assert out2["channels"] == 0 and not captured


def test_closed_live_goes_digest_not_bulletin(db_session):
    """已收尾的直播观点 → 走普通 digest（快报只对进行中的直播）。"""
    _item, live_vps = _mk_live_confirmed(db_session, closed=True)
    ch = _mk_channel(db_session)
    captured: list[httpx.Request] = []
    out = deliver_pending_pushes(db_session, http=_capture(captured))
    assert len(captured) == 1
    text = json.loads(captured[0].read())["content"]["text"]
    assert "新增确认观点" in text and "直播快报" not in text


def test_webhook_bulletin_event_and_fields(db_session):
    """generic_webhook：快报用 viewpoints.live_bulletin 事件，条目带 said_at/live_ongoing。"""
    _item, _vps = _mk_live_confirmed(db_session)
    ch = _mk_channel(db_session, ctype="generic_webhook")
    captured: list[httpx.Request] = []
    deliver_pending_pushes(db_session, http=_capture(captured))
    payload = json.loads(captured[0].read())
    assert payload["event"] == "viewpoints.live_bulletin"
    items = payload["items"]
    assert all(it["live_ongoing"] for it in items)
    assert any(it["said_at"] for it in items)
