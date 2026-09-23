"""开放数据端点（Plan #7）：confirmed 默认、since 增量、分页、transcript、search。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.open import deps as open_deps
from app.db.models import Creator, SourceAccount, SourceItem, TranscriptSegment, Viewpoint
from app.db.session import get_db


@pytest.fixture
def client(db_session, migrated_db, monkeypatch):
    engine = create_engine(migrated_db)
    monkeypatch.setattr(open_deps, "audit_session", sessionmaker(bind=engine))
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


RAW_KEY = "rk_" + "e5f6a7b8" * 4
HEADERS = {"X-API-Key": RAW_KEY}


@pytest.fixture
def seeded(db_session):
    from app.api.open.deps import hash_key
    from app.db.models import ApiKey

    db_session.add(ApiKey(name="k", key_hash=hash_key(RAW_KEY), prefix=RAW_KEY[:8]))
    creator = Creator(display_name="张三", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec1", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id="v1",
        item_type="vod",
        status="ready",
        title="讲黄金",
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(
        TranscriptSegment(
            source_item_id=item.id, sequence_no=1, start_ms=0, end_ms=5000, text="美联储九月必然降息，利好黄金"
        )
    )

    def _vp(status: str, claim: str, updated_delta: timedelta) -> Viewpoint:
        vp = Viewpoint(
            creator_id=creator.id,
            source_item_id=item.id,
            claim=claim,
            stance="bullish",
            confidence=0.8,
            verification_status=status,
        )
        db_session.add(vp)
        db_session.flush()
        vp.updated_at = datetime.now(UTC) + updated_delta
        return vp

    _vp("confirmed", "黄金看涨", timedelta(minutes=-120))
    _vp("confirmed", "黄金还能涨", timedelta(minutes=-5))
    _vp("candidate", "黄金疑似见顶", timedelta(minutes=-1))
    db_session.commit()
    return {"creator_id": creator.id, "item_id": item.id}


def test_default_only_confirmed(client, seeded):
    r = client.get("/open/v1/viewpoints", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2  # candidate 不外泄
    claims = {i["claim"] for i in body["items"]}
    assert "黄金疑似见顶" not in claims


def test_since_incremental(client, seeded):
    # since 语义：updated_at > since；两小时前的旧观点被排除
    since = (datetime.now(UTC) - timedelta(minutes=60)).isoformat()
    r = client.get("/open/v1/viewpoints", headers=HEADERS, params={"since": since})
    items = r.json()["items"]
    assert [i["claim"] for i in items] == ["黄金还能涨"]


def test_explicit_status_and_pagination(client, seeded):
    r = client.get(
        "/open/v1/viewpoints", headers=HEADERS, params={"status": "all", "page_size": 1}
    )
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 1 and body["page"] == 1
    r2 = client.get(
        "/open/v1/viewpoints",
        headers=HEADERS,
        params={"status": "all", "page_size": 1, "page": 2},
    )
    assert len(r2.json()["items"]) == 1


def test_viewpoint_detail_and_transcript_and_search(client, seeded):
    r = client.get("/open/v1/viewpoints", headers=HEADERS)
    vp_id = r.json()["items"][0]["id"]
    detail = client.get(f"/open/v1/viewpoints/{vp_id}", headers=HEADERS)
    assert detail.status_code == 200 and detail.json()["claim"]

    t = client.get(f"/open/v1/items/{seeded['item_id']}/transcript", headers=HEADERS)
    assert t.status_code == 200
    assert t.json()["segments"][0]["text"].startswith("美联储九月必然降息")

    s = client.get("/open/v1/transcripts/search", headers=HEADERS, params={"q": "黄金"})
    assert s.status_code == 200 and s.json()[0]["item_id"] == seeded["item_id"]

    missing = client.get("/open/v1/viewpoints/99999", headers=HEADERS)
    assert missing.status_code == 404


def test_creators_listing(client, seeded):
    r = client.get("/open/v1/creators", headers=HEADERS)
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["display_name"] == "张三"
    assert items[0]["accounts"][0]["platform"] == "douyin"
