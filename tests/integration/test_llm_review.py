"""EPIC-05 LLM 二道复核集成测试：只放行不驳回、修正应用、来源留痕、收尾闸。"""

import json

from app.db.models import SourceItem, Viewpoint
from app.llm.provider import LLMResponse
from app.services.reviewer import llm_review_pending

from tests.integration.test_review_and_history import _mk_item_with_vp


class FakeReviewProvider:
    """按预置 reviews 返回；记录调用入参供断言。"""

    def __init__(self, reviews_by_call: list[list[dict]]):
        self._calls = reviews_by_call
        self.prompts: list[tuple[str, str]] = []
        self.i = 0

    def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
        self.prompts.append((system_prompt, user_prompt))
        reviews = self._calls[min(self.i, len(self._calls) - 1)]
        self.i += 1
        return LLMResponse(
            data={"reviews": reviews},
            usage={"total_tokens": 1},
            provider="fake",
            model=model or "fake",
            raw_content=json.dumps({"reviews": reviews}),
        )


class BadShapeProvider(FakeReviewProvider):
    """复刻 MiniMax 键名漂移：返回非 reviews 键。"""

    def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
        self.prompts.append((system_prompt, user_prompt))
        return LLMResponse(
            data={"viewpoints": []},
            usage={},
            provider="fake",
            model="fake",
        )


def _review(vid, verdict, reason="ok", **extra):
    return {"id": vid, "verdict": verdict, "reason": reason, **extra}


def test_llm_review_accept_confirms_with_correction_and_promotes(db_session, monkeypatch):
    """accept：confirmed + 来源 llm + 立场修正生效；最后一条放行 → ready + 派总结。"""
    vp, item = _mk_item_with_vp(
        db_session,
        claim="黄金难以大跌，短期继续持有",
        stance="bearish",  # 抽取层误判（防御性表述），LLM 应修正为中性
        horizon="1-3M",
    )
    sent = []
    monkeypatch.setattr("app.services.summarizer.dispatch_summarize", lambda iid: sent.append(iid))
    provider = FakeReviewProvider(
        [[_review(vp.id, "accept", reason="证据直接支撑", corrected_stance="neutral")]]
    )
    out = llm_review_pending(db_session, item.id, provider=provider)

    assert out["status"] == "done" and out["confirmed"] == 1 and out["corrected"] == 1
    assert vp.verification_status == "confirmed"
    assert vp.review_source == "llm" and vp.review_note == "证据直接支撑"
    assert vp.stance == "neutral"  # 修正生效
    db_session.expire_all()
    item = db_session.get(SourceItem, item.id)
    assert out["promoted"] is True and item.status == "ready" and sent == [item.id]


def test_llm_review_human_keeps_pending_with_note(db_session):
    """human：留队列、不落 review_source、LLM 意见写 review_note；字符串 id 归一。"""
    vp, item = _mk_item_with_vp(db_session, claim="看涨黄金", stance="bullish")
    provider = FakeReviewProvider([[_review(f"{vp.id}", "human", reason="证据不足")]])
    out = llm_review_pending(db_session, item.id, provider=provider)

    assert out["confirmed"] == 0
    assert vp.verification_status == "needs_review"
    assert vp.review_source is None and vp.review_note == "证据不足"
    assert item.status == "reviewing"


def test_llm_review_ignores_out_of_range_and_bad_ids(db_session):
    """越界 id / 非法 id / 非法修正值全部忽略，不误伤。"""
    vp, item = _mk_item_with_vp(db_session, claim="看涨白银", stance="bullish")
    provider = FakeReviewProvider(
        [
            [
                _review(999999, "accept"),  # 越界
                _review("not-an-int", "accept"),  # 非法 id
                _review(vp.id, "accept", corrected_stance="super_bullish"),  # 非法立场值
            ]
        ]
    )
    out = llm_review_pending(db_session, item.id, provider=provider)
    assert out["confirmed"] == 1  # 只有 vp 放行
    assert vp.stance == "bullish"  # 非法修正未应用


def test_llm_review_skips_when_disabled_or_no_pending(db_session):
    from app.services import live_status
    from app.services.reviewer import llm_review_enabled

    vp, item = _mk_item_with_vp(db_session)
    # 开关关：security 设置落库
    live_status.put_security_settings(db_session, {"llm_review_enabled": False})
    assert llm_review_enabled(db_session) is False
    out = llm_review_pending(db_session, item.id, provider=FakeReviewProvider([[]]))
    assert out["status"] == "skipped_disabled"
    live_status.put_security_settings(db_session, {"llm_review_enabled": True})
    # 无待审：幂等跳过
    vp.verification_status = "confirmed"
    db_session.commit()
    out = llm_review_pending(db_session, item.id, provider=FakeReviewProvider([[]]))
    assert out["status"] == "skipped_no_pending"


def test_llm_review_bad_shape_leaves_queue_intact(db_session):
    """模型输出形状不对：不崩、不动任何观点（留队列等人工/重跑）。"""
    _vp, item = _mk_item_with_vp(db_session)
    out = llm_review_pending(db_session, item.id, provider=BadShapeProvider([[]]))
    assert out["confirmed"] == 0
    assert (
        db_session.query(Viewpoint)
        .filter(
            Viewpoint.source_item_id == item.id,
            Viewpoint.verification_status == "needs_review",
        )
        .count()
        == 1
    )


def test_llm_review_prompt_carries_calibration():
    """system prompt 注入了人工修正校准规则（防占位符漏替换回归）。"""
    from app.llm.calibration import REVIEW_CALIBRATION_MD
    from app.llm.registry import get_prompt_registry
    from app.services.reviewer import REVIEW_PROMPT_VERSION

    pack = get_prompt_registry().get(REVIEW_PROMPT_VERSION)
    assert "__STANCE_CALIBRATION__" in pack.system  # 模板含占位符，渲染时替换
    rendered = pack.system.replace("__STANCE_CALIBRATION__", REVIEW_CALIBRATION_MD)
    assert "__STANCE_CALIBRATION__" not in rendered
    assert "人工修正率最高" in rendered and "回调就是机会" in rendered


def test_security_switch_roundtrip(db_session):
    from app.services.live_status import get_security_settings, put_security_settings

    out = put_security_settings(db_session, {"llm_review_enabled": False})
    assert out["effective"]["llm_review_enabled"] is False
    out = put_security_settings(db_session, {"llm_review_enabled": True})
    assert get_security_settings(db_session)["effective"]["llm_review_enabled"] is True
