"""审核资格与来源指纹计算（insights 域入口，design 2.1 数据归属表）。

观点/证据/审核规则归 insights：待审计数、confirmed 证据清单与来源指纹
（SHA256）在此计算；跨域用例由 workflows 协调时调用，本模块只读不提交。
指纹字节序与原 video_summary_feed 实现完全一致（对外版本兼容，不得改动）。
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.runtime.db.models import (
    Creator,
    Entity,
    SourceAccount,
    SourceItem,
    Viewpoint,
)


def review_state(session: Session, item_id: int) -> tuple[int, int, str]:
    """(待审数, confirmed 数, 指纹)。指纹覆盖 confirmed 证据 + 视频来源上下文。"""
    rows = session.execute(
        select(Viewpoint, Entity.canonical_name)
        .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
        .where(Viewpoint.source_item_id == item_id)
        .order_by(Viewpoint.id)
    ).all()
    item = session.get(SourceItem, item_id)
    source = video_context(session, item) if item else None
    return review_state_from_rows(rows, source)


def review_state_from_rows(rows: Sequence[Any], source: dict | None) -> tuple[int, int, str]:
    pending = sum(v.verification_status in ("candidate", "needs_review") for v, _ in rows)
    confirmed = [(v, name) for v, name in rows if v.verification_status == "confirmed"]
    evidence = [
        [v.id, v.claim, v.stance, v.horizon, v.entity_id, v.entity_raw, name]
        for v, name in confirmed
    ]
    fingerprint = hashlib.sha256(
        json.dumps([evidence, source], ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return pending, len(confirmed), fingerprint


def video_context(session: Session, item: SourceItem) -> dict:
    row = session.execute(
        select(Creator.display_name, SourceAccount.platform)
        .join(SourceAccount, SourceAccount.creator_id == Creator.id)
        .where(SourceAccount.id == item.source_account_id)
    ).first()
    return source_context(item, row[0] if row else None, row[1] if row else None)


def source_context(item: SourceItem, display_name: str | None, platform: str | None) -> dict:
    live = item.item_type == "live"
    return {
        "source_account_id": item.source_account_id,
        "display_name": display_name,
        "video_time": item.published_at.isoformat() if item.published_at else None,
        "time_basis": (
            ("live_started_at" if live else "published_at") if item.published_at else "unknown"
        ),
        "platform": platform,
        "title": item.title,
        "item_type": item.item_type,
    }
