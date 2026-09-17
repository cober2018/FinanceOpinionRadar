from datetime import UTC, datetime

import pytest
from app.db.models import Creator, SourceAccount, SourceItem
from app.services.discovery import (
    create_item_from_url,
    discover_account,
    resolve_url_preview,
)
from app.services.media.contracts import (
    AccountRef,
    DiscoveredItem,
    ResolvedMedia,
    SubtitleTrack,
)

from tests.fixtures.media.payloads import URL


class StubAdapter:
    """够用的桩：resolve/discover 返回固定值，记录调用。"""

    def __init__(self, resolved=None, discovered=None):
        self._resolved = resolved
        self._discovered = discovered
        self.resolve_calls: list[str] = []
        self.discover_calls: list[str] = []

    def resolve(self, url: str):
        self.resolve_calls.append(url)
        if isinstance(self._resolved, Exception):
            raise self._resolved
        return self._resolved

    def discover(self, account: AccountRef):
        self.discover_calls.append(account.external_id)
        if isinstance(self._discovered, Exception):
            raise self._discovered
        return self._discovered


def _resolved(**over) -> ResolvedMedia:
    base: dict = {
        "platform": "youtube",
        "external_item_id":"abc123",
        "title":"美联储加息点评",
        "canonical_url":URL,
        "thumbnail_url":None,
        "duration_ms":1250500,
        "item_type":"vod",
        "published_at":datetime(2026, 3, 15, tzinfo=UTC),
        "channel_external_id":"ch_42",
        "channel_name":"宏观日记",
        "subtitles":(SubtitleTrack("zh-Hans", False),),
        "metadata":{},
        "channel_url": "https://www.youtube.com/@macro-diary",
    }
    base.update(over)
    return ResolvedMedia(**base)


def _make_account(session, *, external_id="ch_42", url=None, mode="manual") -> SourceAccount:
    creator = Creator(display_name="任意作者", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="youtube",
        external_id=external_id,
        url=url,
        discovery_mode=mode,
        poll_interval_sec=3600,
    )
    session.add(account)
    session.flush()
    return account


def test_create_item_from_url_creates_creator_account_item(db_session) -> None:
    adapter = StubAdapter(resolved=_resolved())
    item = create_item_from_url(URL, db_session, adapter)

    assert item.external_item_id == "abc123"
    assert item.status == "discovered"
    account = db_session.get(SourceAccount, item.source_account_id)
    assert account.platform == "youtube" and account.external_id == "ch_42"
    assert account.discovery_mode == "manual"
    assert account.url == "https://www.youtube.com/@macro-diary/videos"  # E3 + 注记③：channel_url 规范化后落账号
    creator = db_session.get(Creator, account.creator_id)
    assert creator.display_name == "宏观日记"


def test_create_item_from_url_is_idempotent(db_session) -> None:
    adapter = StubAdapter(resolved=_resolved())
    first = create_item_from_url(URL, db_session, adapter)
    second = create_item_from_url(URL, db_session, adapter)
    assert first.id == second.id
    assert db_session.query(SourceAccount).count() == 1
    assert db_session.query(SourceItem).count() == 1


def test_create_item_without_channel_falls_back_to_item_account(db_session) -> None:
    adapter = StubAdapter(resolved=_resolved(channel_external_id=None, channel_name=None))
    item = create_item_from_url(URL, db_session, adapter)
    account = db_session.get(SourceAccount, item.source_account_id)
    # C5 回退：账号键 = 视频自身 id，creator 名 = 未知来源
    assert account.external_id == "abc123"
    creator = db_session.get(Creator, account.creator_id)
    assert creator.display_name == "未知来源"


def test_resolve_url_preview_does_not_write(db_session) -> None:
    adapter = StubAdapter(resolved=_resolved())
    media = resolve_url_preview(URL, db_session, adapter)
    assert media.external_item_id == "abc123"
    assert db_session.query(SourceItem).count() == 0
    assert db_session.query(SourceAccount).count() == 0


def test_discover_account_upserts_and_marks_success(db_session) -> None:
    account = _make_account(
        db_session, url="https://www.youtube.com/@x/videos", mode="auto_poll"
    )
    from datetime import UTC, datetime, timedelta

    account.last_success_at = datetime.now(UTC) - timedelta(hours=1)  # 非首扫：跟踪新视频
    db_session.commit()
    items = [
        DiscoveredItem("v1", "一", "https://www.youtube.com/watch?v=v1", None, None, {}),
        DiscoveredItem("v2", "二", "https://www.youtube.com/watch?v=v2", None, None, {}),
    ]
    sent: list[tuple] = []
    adapter = StubAdapter(discovered=items)
    outcome = discover_account(
        account.id, db_session, adapter, send=lambda name, **kw: sent.append((name, kw))
    )

    assert outcome["discovered"] == 2 and outcome["created"] == 2
    assert account.last_success_at is not None
    assert account.failure_count == 0
    assert [kw["args"] for _, kw in sent] == [[1], [2]]
    assert sent[0][0] == "prepare_source_item"

    # 再跑一次：无新建、不重复投递（幂等）
    discover_account(
        account.id, db_session, adapter, send=lambda name, **kw: sent.append((name, kw))
    )
    assert len(sent) == 2


def test_discover_account_skips_missing_or_disabled(db_session) -> None:
    account = _make_account(db_session)
    account.enabled = False
    db_session.flush()
    adapter = StubAdapter(discovered=[])  # discover 不应被调用
    outcome = discover_account(
        999999, db_session, adapter, send=lambda name, **kw: None
    )
    assert outcome["skipped"] is True
    outcome2 = discover_account(
        account.id, db_session, adapter, send=lambda name, **kw: None
    )
    assert outcome2["skipped"] is True
    assert adapter.discover_calls == []


def test_discover_account_failure_increments_counter(db_session) -> None:
    account = _make_account(
        db_session, url="https://www.youtube.com/@x/videos", mode="auto_poll"
    )
    db_session.commit()  # 服务失败路径会 rollback——账号必须先落库，否则连行一起滚掉
    adapter = StubAdapter(discovered=RuntimeError("yt-dlp 崩了"))
    with pytest.raises(RuntimeError):
        discover_account(
            account.id, db_session, adapter, send=lambda name, **kw: None
        )
    db_session.rollback()
    refreshed = db_session.get(SourceAccount, account.id)
    assert refreshed.failure_count == 1
    assert refreshed.last_success_at is None
