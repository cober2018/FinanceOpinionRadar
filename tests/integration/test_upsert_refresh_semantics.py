# Regression: 代码审查 Important-1（2026-09-17，链路第 7 步）——upsert 刷新分支把
# 已解析的 enrichment 值 NULL 掉：flat-playlist（published_at=None）经 ON CONFLICT
# 分支会冲掉 resolve 时落好的日期；channel_url 缺席的二次 create 会冲掉账号 url。
# 规则统一为：可空 enrichment 字段 None 不回写。报告：.gstack/qa-reports/qa-report-localhost-2026-09-17.md
from datetime import UTC, datetime

import pytest
from app.db.models import SourceItem
from app.repositories.source_accounts import SourceAccountRepository
from app.repositories.source_items import SourceItemRepository

from tests.integration.test_discover_tasks import _make_account


def test_refresh_keeps_existing_when_incoming_none_hit_path(db_session) -> None:
    account = _make_account(db_session, external_id="ch_r1")
    db_session.flush()
    repo = SourceItemRepository(db_session)
    resolved_at = datetime(2026, 1, 1, tzinfo=UTC)
    item, created = repo.upsert_by_external(
        source_account_id=account.id,
        external_item_id="v1",
        title="有标题",
        canonical_url="https://www.youtube.com/watch?v=v1",
        published_at=resolved_at,
        thumbnail_url="t.jpg",
        duration_ms=1000,
    )
    assert created
    assert item.title == "有标题"

    # 二次刷新：flat 来源缺 published_at/thumbnail/title 时不得冲掉现值；新值照常更新
    item, created = repo.upsert_by_external(
        source_account_id=account.id,
        external_item_id="v1",
        title=None,
        canonical_url="https://www.youtube.com/watch?v=v1",
        published_at=None,
        thumbnail_url=None,
        duration_ms=2000,
    )
    db_session.flush()
    assert not created
    assert item.title == "有标题"
    assert item.published_at == resolved_at
    assert item.thumbnail_url == "t.jpg"
    assert item.duration_ms == 2000  # 结构性字段仍无条件刷新


def test_refresh_keeps_existing_when_incoming_none_conflict_path(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _make_account(db_session, external_id="ch_r2")
    db_session.flush()
    repo = SourceItemRepository(db_session)
    resolved_at = datetime(2026, 1, 1, tzinfo=UTC)
    repo.upsert_by_external(
        source_account_id=account.id,
        external_item_id="v1",
        title="有标题",
        canonical_url="u",
        published_at=resolved_at,
    )
    db_session.commit()

    # ON CONFLICT 分支在单进程内不可达（显式 SELECT 必命中）；让预检 SELECT 失效一次来驱动它
    real_select = SourceItemRepository._select
    state = {"n": 0}

    def fake_select(self, **kw):
        state["n"] += 1
        return None if state["n"] == 1 else real_select(self, **kw)

    monkeypatch.setattr(SourceItemRepository, "_select", fake_select)
    # 预检被伪造为 miss，repo 必然按"新建"上报；本测试只关心冲突分支落库值
    _, _created = repo.upsert_by_external(
        source_account_id=account.id,
        external_item_id="v1",
        title=None,
        canonical_url="u",
        published_at=None,
    )
    db_session.commit()
    refreshed = (
        db_session.query(SourceItem).filter_by(source_account_id=account.id, external_item_id="v1").one()
    )
    assert refreshed.title == "有标题"
    assert refreshed.published_at == resolved_at


def test_account_url_kept_when_recreate_lacks_channel_url(db_session) -> None:
    account = _make_account(
        db_session, external_id="ch_u", url="https://www.youtube.com/@a/videos"
    )
    db_session.flush()
    assert account.url is not None

    # 二次 upsert（如 channel_url 缺席的 resolve）不得把 url 冲成 NULL
    upserted = SourceAccountRepository(db_session).upsert_by_external(
        creator_id=account.creator_id,
        platform="youtube",
        external_id="ch_u",
        url=None,
    )
    db_session.flush()
    assert upserted.id == account.id
    assert upserted.url == "https://www.youtube.com/@a/videos"
