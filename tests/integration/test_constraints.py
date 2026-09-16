import pytest
from app.db.models import Creator, Entity, SourceAccount, SourceItem, Topic, Viewpoint
from sqlalchemy.exc import IntegrityError


def _account(creator_id: int) -> SourceAccount:
    return SourceAccount(creator_id=creator_id, platform="youtube", external_id="UC_demo")


def _viewpoint(creator_id: int, source_item_id: int, topic_id: int | None = None) -> Viewpoint:
    return Viewpoint(
        creator_id=creator_id,
        source_item_id=source_item_id,
        topic_id=topic_id,
        claim="demo claim",
        stance="bullish",
        horizon="1-3M",
    )


def test_source_account_unique_platform_external(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    db_session.add(_account(creator.id))
    db_session.commit()
    db_session.add(_account(creator.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_source_item_unique_account_external_item(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    account = _account(creator.id)
    db_session.add(account)
    db_session.flush()
    common = {"source_account_id": account.id, "external_item_id": "vid_1"}
    db_session.add(SourceItem(**common))
    db_session.commit()
    db_session.add(SourceItem(**common))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_fk_missing_creator_rejected(db_session) -> None:
    db_session.add(SourceAccount(creator_id=999999, platform="youtube", external_id="x"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_delete_creator_cascades_to_items(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    account = _account(creator.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(SourceItem(source_account_id=account.id, external_item_id="v1"))
    db_session.commit()

    db_session.delete(creator)
    db_session.commit()

    assert db_session.query(SourceAccount).count() == 0
    assert db_session.query(SourceItem).count() == 0


def test_delete_topic_sets_viewpoint_topic_null(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    account = _account(creator.id)
    db_session.add(account)
    db_session.flush()
    item = SourceItem(source_account_id=account.id, external_item_id="v1")
    db_session.add(item)
    topic = Topic(canonical_name="黄金")
    db_session.add(topic)
    db_session.flush()
    db_session.add(_viewpoint(creator.id, item.id, topic.id))
    db_session.commit()

    db_session.delete(topic)
    db_session.commit()

    kept = db_session.query(Viewpoint).one()
    assert kept.topic_id is None  # D56: 删主题不删观点，仅解除关联


def test_delete_entity_sets_viewpoint_entity_null(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    account = _account(creator.id)
    db_session.add(account)
    db_session.flush()
    item = SourceItem(source_account_id=account.id, external_item_id="v1")
    db_session.add(item)
    entity = Entity(entity_type="stock", canonical_name="英伟达")
    db_session.add(entity)
    db_session.flush()
    vp = _viewpoint(creator.id, item.id)
    vp.entity_id = entity.id
    db_session.add(vp)
    db_session.commit()

    db_session.delete(entity)
    db_session.commit()

    kept = db_session.query(Viewpoint).one()
    assert kept.entity_id is None  # D56: 删实体不删观点，仅解除关联
