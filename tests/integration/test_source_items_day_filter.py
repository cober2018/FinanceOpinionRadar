"""周历日期筛选（用户 2026-09-24）：/source-items date_from/date_to 按本机时区当天边界。"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import Creator, SourceAccount, SourceItem
from app.db.session import get_db


@pytest.fixture
def client(db_session):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_day_filter_local_timezone_bounds(db_session, client):
    creator = Creator(display_name="日期主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec_day", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()

    # 今天本地 10:00 与昨天本地 10:00 各一条（本地时区边界，非 UTC 截断）
    base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    ids = {}
    for delta, ext in [(0, "today_v"), (-1, "yesterday_v"), (1, "tomorrow_v")]:
        item = SourceItem(
            source_account_id=account.id,
            external_item_id=ext,
            item_type="vod",
            status="ready",
            published_at=base + timedelta(days=delta),
        )
        db_session.add(item)
        db_session.flush()
        ids[ext] = item.id
    db_session.commit()

    today = base.date().isoformat()
    r = client.get(
        "/api/v1/source-items",
        params={"date_from": today, "date_to": today, "limit": 50},
    )
    assert r.status_code == 200
    got = {row["id"] for row in r.json()}
    assert got == {ids["today_v"]}  # 只命中今天（本机时区）

    # 昨天到今天区间：两条
    yesterday = (base - timedelta(days=1)).date().isoformat()
    r2 = client.get(
        "/api/v1/source-items",
        params={"date_from": yesterday, "date_to": today, "limit": 50},
    )
    got2 = {row["id"] for row in r2.json()}
    assert got2 == {ids["today_v"], ids["yesterday_v"]}

    # 非法日期忽略，不报错
    r3 = client.get("/api/v1/source-items", params={"date_from": "not-a-date"})
    assert r3.status_code == 200
