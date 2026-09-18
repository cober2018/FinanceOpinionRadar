"""live_chat_message 表集成测试（Plan #5 Task 2）：迁移/唯一约束/级联删除。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Creator, LiveChatMessage, SourceAccount, SourceItem


def _make_item(session, *, external_id="MS4wLjABchat1") -> SourceItem:
    creator = Creator(display_name=f"creator_{external_id}", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id,
        platform="douyin",
        external_id=external_id,
        url=f"https://www.douyin.com/user/{external_id}",
        discovery_mode="manual",
        poll_interval_sec=3600,
    )
    session.add(account)
    session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id=f"live:{external_id}:2026-09-18",
        item_type="live",
        title="大潘说股 直播 2026-09-18",
        status="transcribing",
    )
    session.add(item)
    session.flush()
    return item


def test_live_chat_message_roundtrip(db_session):
    item = _make_item(db_session)
    db_session.add(
        LiveChatMessage(
            source_item_id=item.id,
            platform="douyin",
            msg_type="WebcastChatMessage",
            external_msg_id="7299483746218392832",
            user_id="4455667788",
            user_name="小散一枚",
            text="主播怎么看明天的大盘？",
            published_at=datetime.fromtimestamp(1726660425, tz=UTC),
        )
    )
    db_session.commit()
    db_session.expire_all()
    row = db_session.query(LiveChatMessage).one()
    assert row.msg_type == "WebcastChatMessage"
    assert row.user_name == "小散一枚"
    assert row.published_at is not None
    assert row.received_at is not None


def test_live_chat_message_unique_constraint(db_session):
    item = _make_item(db_session)
    db_session.add(
        LiveChatMessage(
            source_item_id=item.id, msg_type="WebcastChatMessage", external_msg_id="m1"
        )
    )
    db_session.commit()
    db_session.add(
        LiveChatMessage(
            source_item_id=item.id, msg_type="WebcastChatMessage", external_msg_id="m1"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_live_chat_message_cascade_delete(db_session):
    item = _make_item(db_session)
    db_session.add(
        LiveChatMessage(
            source_item_id=item.id, msg_type="WebcastGiftMessage", external_msg_id="g1"
        )
    )
    db_session.commit()
    db_session.delete(item)
    db_session.commit()
    assert db_session.query(LiveChatMessage).count() == 0
