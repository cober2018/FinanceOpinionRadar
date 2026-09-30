"""直播滚动总结集成测试（2026-09-30）：增量抽取水位幂等、滚动总结跳门禁、close 收尾。"""

import json
from datetime import UTC, datetime, timedelta

from app.db.models import (
    Creator,
    LiveChatMessage,
    MediaAsset,
    SourceAccount,
    SourceItem,
    TranscriptSegment,
    Viewpoint,
    ViewpointEvidence,
)
from app.llm.provider import LLMResponse
from app.services.live_rolling import (
    extract_live_incremental,
    finalize_after_close,
    rolling_summarize_live,
)

from tests.integration.test_review_and_history import _mk_item_with_vp


class FakeExtractProvider:
    """按预置候选返回（extraction schema 形状）。"""

    def __init__(self, candidates: list[dict]):
        self._candidates = candidates
        self.calls = 0

    def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
        self.calls += 1
        return LLMResponse(
            data={"viewpoints": self._candidates if self.calls == 1 else []},
            usage={"total_tokens": 1},
            provider="fake",
            model="fake",
        )


def _mk_live_item(db_session, *, published_at=None, segments_text=("大盘今天看涨",)):
    """直播条目 + 若干转录段（start_ms 为直播内全局偏移）。"""
    from app.db.models import TranscriptSegment

    creator = Creator(display_name="滚动主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=f"live_{creator.id}",
        discovery_mode="manual",
        enabled=True,
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"live_session_{creator.id}",
        item_type="live",
        status="transcribing",
        published_at=published_at or datetime.now(UTC),
        metadata_json={"live": {"processed": {"1": {"duration_ms": 60000, "at": datetime.now(UTC).isoformat()}}, "closed": False}},
    )
    db_session.add(item)
    db_session.flush()
    for i, text in enumerate(segments_text):
        db_session.add(
            TranscriptSegment(
                source_item_id=item.id,
                sequence_no=i + 1,
                start_ms=i * 30000,
                end_ms=(i + 1) * 30000,
                text=text,
            )
        )
    db_session.commit()
    return item


def test_extract_live_incremental_watermark_idempotent(db_session, monkeypatch):
    """首轮抽水位段并推进水位；二轮无新段跳过；新段落库后只抽新段。"""
    item = _mk_live_item(db_session, segments_text=("黄金短期看涨，避险情绪升温，美联储降息预期推动资金持续流入黄金ETF，避险与投机双轮驱动，金价有望继续上攻。", "大盘中期继续看多，流动性改善背景下指数有望震荡上行，关注量能变化。"))
    sent_llm_review: list[int] = []
    sent_summarize: list[int] = []
    monkeypatch.setattr("app.services.reviewer.dispatch_llm_review", lambda iid: sent_llm_review.append(iid))
    monkeypatch.setattr(
        "app.services.live_rolling.dispatch_rolling_summarize", lambda iid: sent_summarize.append(iid)
    )
    provider = FakeExtractProvider(
        [
            {
                "claim": "黄金短期看涨",
                "stance": "bullish",
                "confidence": 0.9,
                "evidence_segment_ids": [1],
                "entities": [],
            }
        ]
    )
    out = extract_live_incremental(db_session, item.id, provider=provider)
    assert out["status"] == "done" and out["created"] == 1
    # 水位推进到最后一段 end_ms
    db_session.expire_all()
    item = db_session.get(SourceItem, item.id)
    assert (item.metadata_json["live"]["incremental_extracted_ms"]) == 60000
    # 观点已规则复核（证据够 → confirmed）
    vp = db_session.query(Viewpoint).filter_by(source_item_id=item.id).one()
    assert vp.verification_status == "confirmed" and vp.review_source == "rule"
    # 无 needs_review → 直接派滚动总结
    assert sent_llm_review == [] and sent_summarize == [item.id]

    # 二轮：无新段，幂等跳过
    out2 = extract_live_incremental(db_session, item.id, provider=provider)
    assert out2["status"] == "skipped_no_new_segments"


def test_rolling_summarize_overwrites_without_gate(db_session, monkeypatch):
    """滚动总结不受「全部确认」门禁限制（直播中总有候选在途），直接覆盖写。"""
    vp, item = _mk_item_with_vp(db_session, claim="看涨黄金", stance="bullish")
    item.item_type = "live"
    item.status = "transcribing"
    vp.verification_status = "confirmed"
    db_session.commit()

    class FakeSummaryProvider:
        def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
            return LLMResponse(
                data={"summary": "黄金短期看涨，避险情绪主导。"},
                usage={},
                provider="fake",
                model="fake",
            )

    out = rolling_summarize_live(db_session, item.id, provider=FakeSummaryProvider())
    assert out["status"] == "done"
    db_session.expire_all()
    item = db_session.get(SourceItem, item.id)
    assert item.viewpoint_summary == "黄金短期看涨，避险情绪主导。"
    assert item.summary_generated_at is not None
    # 状态未被推进（滚动总结不碰状态机）
    assert item.status == "transcribing"


def test_rolling_summarize_skips_no_confirmed(db_session):
    item = _mk_live_item(db_session)
    out = rolling_summarize_live(db_session, item.id)
    assert out["status"] == "skipped_no_confirmed"


def test_finalize_after_close_promotes(db_session, monkeypatch):
    """close 收尾：transcribed → reviewing 推进；无待审 → ready + 派总结。"""
    vp, item = _mk_item_with_vp(db_session, claim="看涨黄金", stance="bullish", status="transcribed")
    item.item_type = "live"
    vp.verification_status = "confirmed"
    db_session.commit()
    sent = []
    monkeypatch.setattr("app.services.summarizer.dispatch_summarize", lambda iid: sent.append(iid))
    out = finalize_after_close(db_session, item.id)
    db_session.expire_all()
    item = db_session.get(SourceItem, item.id)
    assert out["promoted"] is True and item.status == "ready" and sent == [item.id]


def test_timeline_orders_desc_and_offsets_live(db_session):
    """时间轴：at 倒序；live 观点时刻 = 起播 + 证据偏移；总结事件带直播中标记。"""
    from fastapi.testclient import TestClient

    from app.main import app

    base = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    # 复用 helper 建 vod item + 观点（confirmed）
    vp, vod_item = _mk_item_with_vp(
        db_session, claim="vod 看涨黄金", stance="bullish", status="reviewing"
    )
    vp.verification_status = "confirmed"
    vod_item.item_type = "vod"
    vod_item.published_at = base
    db_session.commit()

    # live item：起播 base-2h，一个 30s 偏移的段 + confirmed 观点
    live_item = _mk_live_item(
        db_session, published_at=base - timedelta(hours=2), segments_text=("黄金大涨逻辑。",)
    )
    creator_id = db_session.query(SourceAccount).filter_by(id=live_item.source_account_id).one().creator_id
    live_vp = Viewpoint(
        creator_id=creator_id,
        source_item_id=live_item.id,
        claim="直播看涨黄金",
        stance="bullish",
        confidence=0.9,
        importance=0.5,
        verification_status="confirmed",
        as_of_date=(base - timedelta(hours=2)).date(),
    )
    db_session.add(live_vp)
    db_session.flush()
    seg = db_session.query(TranscriptSegment).filter_by(source_item_id=live_item.id).one()
    db_session.add(
        ViewpointEvidence(
            viewpoint_id=live_vp.id,
            transcript_segment_id=seg.id,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            evidence_text=seg.text,
            evidence_order=1,
        )
    )
    # live 滚动总结
    live_item.viewpoint_summary = "直播滚动总结文本"
    live_item.summary_generated_at = base + timedelta(minutes=30)
    db_session.commit()

    from app.db.session import get_db

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    day = base.date().isoformat()
    resp = client.get(f"/api/v1/timeline?date_from={day}&date_to={day}")
    assert resp.status_code == 200
    events = resp.json()["events"]
    types = {e["type"] for e in events}
    assert types <= {"viewpoint", "summary"}
    # at 倒序
    ats = [e["at"] for e in events]
    assert ats == sorted(ats, reverse=True)
    live_vp_ev = next(e for e in events if e.get("viewpoint_id") == live_vp.id)
    # live 时刻 = 起播(08:00) + 0s 偏移 = 08:00，而非发布当天其他时间
    assert live_vp_ev["at"].startswith((base - timedelta(hours=2)).date().isoformat())
    summ_ev = next(e for e in events if e["type"] == "summary" and e["item_id"] == live_item.id)
    assert summ_ev["live_ongoing"] is True and summ_ev["summary"] == "直播滚动总结文本"
