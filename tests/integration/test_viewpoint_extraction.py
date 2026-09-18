"""观点抽取集成测试（EPIC-04 RAD-043）：真库全链——校验/证据/幂等/状态机/存档。

LLM 用固定输出的 stub provider（不打桩 httpx，直接注入 provider 参数）。
"""

from datetime import UTC, datetime

import pytest
from app.db.models import (
    Creator,
    Entity,
    EntityCandidate,
    SourceAccount,
    SourceItem,
    TranscriptSegment,
    Viewpoint,
    ViewpointEvidence,
)
from app.llm.provider import LLMResponse
from app.services.extraction import extract_source_item


class StubLLM:
    """按预设脚本返回候选；usage 全 0。"""

    def __init__(self, script: list[dict]):
        self.script = script  # 每个 chunk 一个响应 data
        self.calls = 0

    def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
        data = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return LLMResponse(data=data, usage={"total_tokens": 0}, provider="stub", model="stub")


@pytest.fixture
def tracked_transcribed_item(db_session):
    """auto_poll 账号 + 已转写 item + 3 个转录段。"""
    creator = Creator(display_name="测试主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id="MS4wLjABext01",
        url="https://www.douyin.com/user/MS4wLjABext01",
        discovery_mode="auto_poll",
        enabled=True,
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id="v_test_1",
        title="测试视频",
        item_type="vod",
        status="transcribed",
        published_at=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
        metadata_json={},
    )
    db_session.add(item)
    db_session.flush()
    for i, text in enumerate(
        [
            "我认为美联储九月必然降息几乎是确定的事情，这是当前市场最大的宏观主线。",
            "利好黄金，避险需求会推动金价看涨到年底，这个逻辑非常清晰值得认真对待。",
            "以上就是我的全部判断。",
        ],
        start=1,
    ):
        db_session.add(
            TranscriptSegment(
                source_item_id=item.id,
                sequence_no=i,
                start_ms=(i - 1) * 5000,
                end_ms=i * 5000,
                text=text,
            )
        )
    db_session.commit()
    return item


GOOD_CAND = {
    "claim": "美联储九月必然降息，利好黄金",
    "stance": "bullish",
    "horizon": "1-3M",
    "confidence": 0.9,
    "importance": 0.8,
    "topic": "美联储利率",
    "conditional": False,
    "entities": [{"raw_name": "黄金", "entity_type": "commodity"}],
    "evidence_segment_ids": [1, 2],
}


def test_extraction_happy_path(db_session, tracked_transcribed_item):
    provider = StubLLM([{"viewpoints": [GOOD_CAND]}])
    item_id = tracked_transcribed_item.id

    out = extract_source_item(db_session, item_id, provider=provider)
    assert out["created"] == 1 and out["rejected"] == 0

    vp = db_session.query(Viewpoint).filter(Viewpoint.source_item_id == item_id).one()
    # EPIC-05 reviewer：证据充分（67 字 ≥ 50）+ 置信 0.9 → accept → confirmed
    assert vp.stance == "bullish" and vp.verification_status == "confirmed"
    assert out["review"]["confirmed"] == 1
    assert vp.extractor_version == "v1" and vp.prompt_version == "extraction@v1"
    assert vp.as_of_date == datetime(2026, 9, 17, tzinfo=UTC).date()
    # 证据绑定：2 条、文本来自转录段
    evs = db_session.query(ViewpointEvidence).filter_by(viewpoint_id=vp.id).all()
    assert len(evs) == 2
    assert {ev.evidence_text for ev in evs} == {
        "我认为美联储九月必然降息几乎是确定的事情，这是当前市场最大的宏观主线。",
        "利好黄金，避险需求会推动金价看涨到年底，这个逻辑非常清晰值得认真对待。",
    }
    # 实体归一：黄金不在词典 → entity_candidate
    assert vp.entity_id is None
    cand = db_session.query(EntityCandidate).filter_by(raw_name="黄金").one()
    assert cand.entity_type == "commodity"
    # 状态推进：transcribed → extracting → reviewing
    db_session.expire(tracked_transcribed_item)
    assert tracked_transcribed_item.status == "reviewing"
    # run 存档索引
    runs = tracked_transcribed_item.metadata_json["llm_runs"]
    assert len(runs) == 1 and runs[0]["created"] == 1 and "llm-runs/" in runs[0]["uri"]


def test_extraction_rejects_invalid_candidates(db_session, tracked_transcribed_item):
    bad_stance = {**GOOD_CAND, "claim": "观点A", "stance": "看涨", "evidence_segment_ids": [1]}
    out_of_chunk = {**GOOD_CAND, "claim": "观点B", "evidence_segment_ids": [999]}
    no_evidence = {**GOOD_CAND, "claim": "观点C", "evidence_segment_ids": []}
    bad_conf = {**GOOD_CAND, "claim": "观点D", "confidence": 1.5, "evidence_segment_ids": [1]}
    provider = StubLLM(
        [{"viewpoints": [bad_stance, out_of_chunk, no_evidence, bad_conf, GOOD_CAND]}]
    )

    out = extract_source_item(db_session, tracked_transcribed_item.id, provider=provider)
    assert out["created"] == 1 and out["rejected"] == 4  # 只有 GOOD_CAND 落库
    assert db_session.query(Viewpoint).count() == 1


def test_extraction_idempotent_same_versions(db_session, tracked_transcribed_item):
    provider = StubLLM([{"viewpoints": [GOOD_CAND]}, {"viewpoints": [GOOD_CAND]}])
    extract_source_item(db_session, tracked_transcribed_item.id, provider=provider)
    out2 = extract_source_item(db_session, tracked_transcribed_item.id, provider=provider)
    assert out2["skipped"] == "already_extracted"
    assert db_session.query(Viewpoint).count() == 1  # 不双写


def test_dedupe_merges_similar_same_stance(db_session, tracked_transcribed_item):
    # 两条高相似观点（同实体缺省=None 同 stance）→ 合并为 1，证据吸收
    a = {**GOOD_CAND, "claim": "美联储九月必然降息利好黄金价格"}
    b = {**GOOD_CAND, "claim": "美联储九月必然降息，利好黄金价格走势", "evidence_segment_ids": [3]}
    provider = StubLLM([{"viewpoints": [a, b]}])
    extract_source_item(db_session, tracked_transcribed_item.id, provider=provider)

    vps = db_session.query(Viewpoint).filter(Viewpoint.source_item_id == tracked_transcribed_item.id).all()
    assert len(vps) == 1
    assert "相似度" in (vps[0].merge_reason or "")
    # 被吸收观点的证据并入 keeper：段 1/2/3 全在
    evs = db_session.query(ViewpointEvidence).filter_by(viewpoint_id=vps[0].id).all()
    all_text = " ".join(ev.evidence_text for ev in evs)
    assert "美联储九月必然降息" in all_text
    assert "以上就是我的全部判断" in all_text


def test_extraction_non_auto_account_not_dispatched_is_orthogonal(db_session, tracked_transcribed_item):
    """手动端点/扫库的语义边界：backfill 条目不自动抽取由 sweep 过滤保证（任务层）。"""
    tracked_transcribed_item.metadata_json = {"backfill": True}
    db_session.commit()
    provider = StubLLM([{"viewpoints": [GOOD_CAND]}])
    # 服务本身允许手动抽取（幂等键拦截重复）——此处验证 backfill 不影响直接调用
    out = extract_source_item(db_session, tracked_transcribed_item.id, provider=provider)
    assert out["created"] == 1


def test_entity_normalizer_four_steps(db_session):
    from app.services.entity_normalizer import normalize_entity

    db_session.add(
        Entity(
            entity_type="stock",
            canonical_name="贵州茅台",
            symbol="600519",
            aliases=["茅台", "MOUTAI"],
        )
    )
    db_session.commit()

    assert normalize_entity(db_session, "贵州茅台")[1] == "exact"
    assert normalize_entity(db_session, "茅台")[1] == "alias"
    assert normalize_entity(db_session, "600519")[1] == "symbol"
    assert normalize_entity(db_session, "贵州茅台酒")[0] is not None  # fuzzy 唯一命中
    # 双向包含多命中（canonical 互含）→ candidate，绝不猜
    db_session.add(Entity(entity_type="institution", canonical_name="茅台集团"))
    db_session.add(Entity(entity_type="institution", canonical_name="茅台集团财务公司"))
    db_session.commit()
    eid, disp = normalize_entity(db_session, "茅台集团财务", entity_type="institution")
    assert eid is None and disp == "candidate"  # 同时包含于两个 canonical → 拒绝猜测
    eid, disp2 = normalize_entity(db_session, "完全未知标的", entity_type="stock")
    assert eid is None and disp2 == "candidate"
    assert db_session.query(EntityCandidate).filter_by(raw_name="完全未知标的").count() == 1
