"""视频观点一句话总结（用户 2026-09-24）：LLM 整合 confirmed 观点写回条目。"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.db.models import Creator, SourceAccount, SourceItem, Viewpoint
from app.services.summarizer import summarize_source_item


class StubLLM:
    def __init__(self, data: dict):
        self.data = data
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
        self.calls.append((system_prompt, user_prompt))
        return SimpleNamespace(data=self.data, raw_head="")


def _mk_item_with_confirmed(db_session, *, confirmed=2, pending=0) -> SourceItem:
    creator = Creator(display_name="总结主播", status="active")
    db_session.add(creator)
    db_session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="douyin", external_id="sec_sum", discovery_mode="auto_poll"
    )
    db_session.add(account)
    db_session.flush()
    item = SourceItem(
        source_account_id=account.id,
        external_item_id="sum_v1",
        item_type="vod",
        status="ready",
        title="9月24日复盘",
        published_at=datetime(2026, 9, 24, tzinfo=UTC),
    )
    db_session.add(item)
    db_session.flush()
    for i in range(confirmed):
        db_session.add(
            Viewpoint(
                creator_id=creator.id,
                source_item_id=item.id,
                claim=f"大盘{i}看涨",
                stance="bullish",
                verification_status="confirmed",
            )
        )
    for i in range(pending):
        db_session.add(
            Viewpoint(
                creator_id=creator.id,
                source_item_id=item.id,
                claim=f"待审观点{i}",
                stance="neutral",
                verification_status="needs_review",
            )
        )
    db_session.commit()
    return item


def test_summarize_writes_back(db_session):
    item = _mk_item_with_confirmed(db_session)
    llm = StubLLM({"summary": "该视频对大盘整体看涨，认为流动性宽松将推动短期上行。"})

    out = summarize_source_item(db_session, item.id, provider=llm)
    assert out["status"] == "done" and "看涨" in out["summary"]
    db_session.expire_all()
    item_db = db_session.get(SourceItem, item.id)
    assert item_db.viewpoint_summary and item_db.summary_generated_at is not None
    # prompt 带上了 confirmed 观点
    assert "大盘0看涨" in llm.calls[0][1]


def test_summarize_skips_without_confirmed(db_session):
    item = _mk_item_with_confirmed(db_session, confirmed=0, pending=1)
    llm = StubLLM({"summary": "x"})
    out = summarize_source_item(db_session, item.id, provider=llm)
    assert out["status"] == "skipped_no_confirmed" and llm.calls == []
    db_session.expire_all()
    assert db_session.get(SourceItem, item.id).viewpoint_summary is None


def test_summarize_key_drift_fallback(db_session):
    """模型键名漂移但唯一字符串值 → 兜底采纳（MiniMax 实录行为）。"""
    item = _mk_item_with_confirmed(db_session)
    llm = StubLLM({"text": "键名漂移的总结内容。"})
    out = summarize_source_item(db_session, item.id, provider=llm)
    assert out["status"] == "done" and out["summary"] == "键名漂移的总结内容。"
