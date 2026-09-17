"""TranscriptRepository 集成测试（RAD-034）：替换语义 / 连续编号 / 非法段回滚。"""

import pytest
from app.repositories.source_items import SourceItemRepository
from app.repositories.transcripts import TranscriptRepository, TranscriptWrite

from tests.integration.test_discover_tasks import _make_account


def _make_item(db_session, external_id: str = "v1"):
    account = _make_account(db_session, external_id=f"acc_{external_id}")
    db_session.flush()
    item, _created = SourceItemRepository(db_session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=external_id,
        title="测试条目",
        canonical_url=f"https://www.youtube.com/watch?v={external_id}",
        item_type="vod",
    )
    db_session.flush()
    return item


def test_replace_for_item_swaps_atomically(db_session) -> None:
    item = _make_item(db_session)
    repo = TranscriptRepository(db_session)
    n = repo.replace_for_item(
        item.id,
        [
            TranscriptWrite(start_ms=0, end_ms=1000, text="一", language="zh"),
            TranscriptWrite(
                start_ms=1500, end_ms=2000, text="二", confidence=0.9, speaker="S1"
            ),
        ],
    )
    assert n == 2
    assert repo.count_for_item(item.id) == 2

    # 重跑替换语义：旧段全清，新段连续从 0 编号（ENG-4A：显式列映射）
    repo.replace_for_item(item.id, [TranscriptWrite(start_ms=0, end_ms=500, text="新")])
    db_session.flush()
    rows = repo.list_for_item(item.id)
    assert [r.text for r in rows] == ["新"]
    assert rows[0].sequence_no == 0
    assert rows[0].start_ms == 0 and rows[0].end_ms == 500


def test_write_maps_to_model_columns(db_session) -> None:
    # ENG-4A 回归：confidence→asr_confidence、speaker→speaker_label
    item = _make_item(db_session, external_id="v3")
    repo = TranscriptRepository(db_session)
    repo.replace_for_item(
        item.id,
        [
            TranscriptWrite(
                start_ms=0, end_ms=800, text="映射", confidence=0.9123, speaker="SPEAKER_00"
            )
        ],
    )
    db_session.flush()
    row = repo.list_for_item(item.id)[0]
    assert float(row.asr_confidence) == pytest.approx(0.9123)
    assert row.speaker_label == "SPEAKER_00"
    assert row.language is None


def test_replace_rolls_back_on_bad_segment(db_session) -> None:
    item = _make_item(db_session, external_id="v4")
    repo = TranscriptRepository(db_session)
    repo.replace_for_item(item.id, [TranscriptWrite(start_ms=0, end_ms=1000, text="旧")])
    db_session.flush()

    with pytest.raises(ValueError, match="时间非法"):
        repo.replace_for_item(
            item.id,
            [
                TranscriptWrite(start_ms=0, end_ms=500, text="好段"),
                TranscriptWrite(start_ms=800, end_ms=800, text="零长段"),
            ],
        )
    # 校验先行：抛错时 delete 从未执行，已 flush 的旧数据完好
    assert repo.count_for_item(item.id) == 1
    assert repo.list_for_item(item.id)[0].text == "旧"
