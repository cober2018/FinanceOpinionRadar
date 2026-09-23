"""EPIC-05/06 集成测试：reviewer 裁决、review API + 审计、快照/change_type/共识/过期。"""

from datetime import UTC, datetime, timedelta

from app.db.models import (
    AuditLog,
    Creator,
    CreatorTopicSnapshot,
    SourceAccount,
    SourceItem,
    Topic,
    TranscriptSegment,
    Viewpoint,
)
from app.services.history import (
    apply_expiry,
    build_snapshot,
    build_topic_consensus,
    compute_change_type,
)
from app.services.reviewer import review_candidate
from fastapi.testclient import TestClient


def _mk_item_with_vp(
    db_session,
    *,
    claim="看涨黄金",
    stance="bullish",
    horizon="1-3M",
    confidence=0.9,
    topic_name="美联储利率",
    status="reviewing",
    as_of=None,
    evidence="我认为美联储九月必然降息几乎是确定的事情，黄金作为避险资产会有一波明确的上涨行情，可以看涨到年底。这个逻辑链条非常清晰，值得认真对待。",
):
    from app.db.models import ViewpointEvidence

    creator = Creator(display_name=f"主播{claim[:4]}", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=f"ext_{claim[:6]}_{creator.id}",
        discovery_mode="auto_poll",
        enabled=True,
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"v_{creator.id}_{claim[:8]}",
        item_type="vod",
        status=status,
        published_at=as_of or datetime.now(UTC),
    )
    db_session.add(item)
    db_session.flush()
    seg = TranscriptSegment(
        source_item_id=item.id, sequence_no=1, start_ms=0, end_ms=5000, text=evidence
    )
    db_session.add(seg)
    topic = db_session.query(Topic).filter_by(canonical_name=topic_name).one_or_none()
    if topic is None:
        topic = Topic(canonical_name=topic_name)
        db_session.add(topic)
        db_session.flush()
    vp = Viewpoint(
        creator_id=creator.id,
        source_item_id=item.id,
        topic_id=topic.id,
        claim=claim,
        stance=stance,
        horizon=horizon,
        confidence=confidence,
        importance=0.7,
        verification_status="needs_review",
        as_of_date=(as_of or datetime.now(UTC)).date(),
        extractor_version="v1",
        prompt_version="extraction@v1",
    )
    db_session.add(vp)
    db_session.flush()
    db_session.add(
        ViewpointEvidence(
            viewpoint_id=vp.id,
            transcript_segment_id=seg.id,
            start_ms=0,
            end_ms=5000,
            evidence_text=evidence,
            evidence_order=1,
        )
    )
    db_session.commit()
    return vp, item


# --- RAD-050 reviewer 规则 ---


def test_reviewer_accept_high_confidence_with_evidence(db_session):
    vp, _ = _mk_item_with_vp(db_session, confidence=0.9)
    d = review_candidate(db_session, vp)
    assert d.decision == "accept" and d.evidence_sufficient and not d.is_quoted_other_person


def test_reviewer_flags_quoted_other_person(db_session):
    vp, _ = _mk_item_with_vp(
        db_session,
        claim="市场传闻要降息了",
        confidence=0.9,
        evidence="我认为美联储九月必然降息几乎是确定的事情，黄金作为避险资产会有一波明确的上涨行情，大家可以关注一下。",
    )
    d = review_candidate(db_session, vp)
    assert d.is_quoted_other_person and d.decision == "needs_review"


def test_reviewer_insufficient_evidence_needs_review(db_session):
    # 证据"不足"（有但少于阈值）→ 进人工队列；完全无证据才直接 reject
    vp, _ = _mk_item_with_vp(db_session, confidence=0.9, evidence="太短")
    d = review_candidate(db_session, vp)
    assert d.decision == "needs_review" and not d.evidence_sufficient


def test_reviewer_unclear_stance_needs_review(db_session):
    vp, _ = _mk_item_with_vp(db_session, stance="unclear", confidence=0.9)
    assert review_candidate(db_session, vp).decision == "needs_review"


# --- RAD-052 review API + 审计 ---


def test_confirm_reject_patch_write_audit_and_ready(db_session):
    from app.db.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    vp, item = _mk_item_with_vp(db_session)
    vp2, _item2 = _mk_item_with_vp(db_session, claim="看跌美元", stance="bearish")

    r = client.post(f"/api/v1/viewpoints/{vp.id}/confirm", params={"reason": "证据充分"})
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.get(Viewpoint, vp.id).verification_status == "confirmed"
    # confirm 触发快照（topic 美联储利率）
    snap = db_session.query(CreatorTopicSnapshot).one_or_none()
    assert snap is not None and snap.change_type == "new_thesis"
    # item 无剩余待审 → ready
    db_session.expire(item)
    assert item.status == "ready"

    r = client.post(f"/api/v1/viewpoints/{vp2.id}/reject", params={"reason": "转述他人"})
    assert r.status_code == 200

    r = client.patch(
        f"/api/v1/viewpoints/{vp.id}",
        json={"stance": "strong_bullish", "reason": "原话强调了'必然'"},
    )
    assert r.status_code == 200

    audits = db_session.query(AuditLog).order_by(AuditLog.id).all()
    assert [a.action for a in audits] == ["confirm", "reject", "patch"]
    assert audits[0].actor == "console" and audits[0].before_json["verification_status"] == "needs_review"


def test_patch_entity_name_hit_miss_and_clear(db_session):
    from app.db.models import Entity, EntityCandidate
    from app.db.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    db_session.add(Entity(entity_type="stock", canonical_name="英伟达"))
    db_session.commit()
    vp, _ = _mk_item_with_vp(db_session)

    # 词典命中 → 挂 entity_id（与抽取一致：raw 保留原始名）
    r = client.patch(f"/api/v1/viewpoints/{vp.id}", json={"entity_name": "英伟达"})
    assert r.status_code == 200
    db_session.expire_all()
    vp_db = db_session.get(Viewpoint, vp.id)
    assert vp_db.entity_id is not None and vp_db.entity_raw == "英伟达"

    # 未命中 → entity_id 置空、entity_raw 回落展示，且入 candidate 待晋升
    r = client.patch(f"/api/v1/viewpoints/{vp.id}", json={"entity_name": "中证500"})
    assert r.status_code == 200
    db_session.expire_all()
    vp_db = db_session.get(Viewpoint, vp.id)
    assert vp_db.entity_id is None and vp_db.entity_raw == "中证500"
    cand = (
        db_session.query(EntityCandidate).filter_by(raw_name="中证500").one_or_none()
    )
    assert cand is not None

    # 空串 = 清除标的
    r = client.patch(f"/api/v1/viewpoints/{vp.id}", json={"entity_name": " "})
    assert r.status_code == 200
    db_session.expire_all()
    vp_db = db_session.get(Viewpoint, vp.id)
    assert vp_db.entity_id is None and vp_db.entity_raw is None

    # 审计展开为 entity_id/entity_raw 的前后值
    audits = db_session.query(AuditLog).filter_by(action="patch").all()
    assert any(
        set(a.before_json) >= {"entity_id", "entity_raw"} for a in audits
    )


def test_patch_entity_conflict_and_bad_id_rejected(db_session):
    from app.db.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    vp, _ = _mk_item_with_vp(db_session)

    r = client.patch(
        f"/api/v1/viewpoints/{vp.id}", json={"entity_id": 1, "entity_name": "英伟达"}
    )
    assert r.status_code == 422
    r = client.patch(f"/api/v1/viewpoints/{vp.id}", json={"entity_id": 99999})
    assert r.status_code == 422


# --- RAD-060 change_type 规则 ---


def _vp(claim="c", stance="bullish", horizon="1-3M"):
    return Viewpoint(claim=claim, stance=stance, horizon=horizon)


def test_change_type_rules():
    f = compute_change_type
    assert f(None, _vp()) == "new_thesis"
    assert f(_vp(stance="unclear"), _vp()) == "new_thesis"
    assert f(_vp(stance="neutral"), _vp()) == "stance_flip"
    assert f(_vp(stance="bearish"), _vp()) == "stance_flip"
    assert f(_vp(stance="bullish", horizon="1-3M"), _vp()) == "repeated"
    assert f(_vp(stance="bullish", horizon="1-3M"), _vp(stance="strong_bullish")) == "strengthening"
    assert f(_vp(stance="strong_bullish"), _vp(stance="bullish")) == "weakening"
    assert f(_vp(stance="bullish", horizon="1-3D"), _vp(horizon="1-3M")) == "horizon_change"
    # stance 与 horizon 同时变 → stance_flip 优先
    assert f(_vp(stance="bearish", horizon="1-3D"), _vp(horizon="1-3M")) == "stance_flip"


# --- RAD-061/062 共识与过期 ---


def test_consensus_and_expiry(db_session):
    topic = Topic(canonical_name="黄金")
    db_session.add(topic)
    db_session.flush()
    creator = Creator(display_name="共识主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="cons1", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id="v_cons_1",
        item_type="vod",
        status="ready",
    )
    db_session.add(item)
    db_session.flush()

    base = datetime.now(UTC).date() - timedelta(days=5)
    vp = Viewpoint(
        creator_id=creator.id,
        source_item_id=item.id,
        topic_id=topic.id,
        claim="看涨黄金",
        stance="bullish",
        horizon="1-3D",
        confidence=0.9,
        as_of_date=base,
        verification_status="confirmed",
    )
    db_session.add(vp)
    db_session.commit()

    # 快照 + 当日共识
    build_snapshot(db_session, vp)
    assert db_session.query(CreatorTopicSnapshot).count() == 1
    row = build_topic_consensus(db_session, topic.id, base)
    assert row.creator_count == 1 and row.bullish_count == 1
    assert float(row.net_stance_score) == 1.0
    assert float(row.disagreement_score) == 0.0

    # 过期：1-3D 有效 3 天，5 天后过期 → snapshot 标 expired，共识剔除
    marked = apply_expiry(db_session, today=datetime.now(UTC).date())
    assert marked == 1
    snap = db_session.query(CreatorTopicSnapshot).one()
    assert snap.change_type == "expired"
    row2 = build_topic_consensus(db_session, topic.id, datetime.now(UTC).date())
    assert row2.creator_count == 0 and row2.bullish_count == 0
