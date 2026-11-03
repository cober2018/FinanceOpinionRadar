"""SourceItem 总结字段写入口（content 域，design 2.1 数据归属表）。

总结文本由 insights 计算、经 workflows 协调调用这里保存到 content 拥有的
SourceItem 现有字段（viewpoint_summary / summary_generated_at），不新建第二份
总结真源。本入口只修改不提交（design 2.2：提交责任在协调入口）。
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.runtime.db.models import SourceItem

SUMMARY_MAX_CHARS = 500


def save_viewpoint_summary(
    session: Session,
    item: SourceItem,
    summary: str,
    *,
    generated_at: datetime | None = None,
) -> None:
    """写回一句话总结（统一 500 字截断口径；generated_at 缺省为当前时刻）。"""
    item.viewpoint_summary = summary[:SUMMARY_MAX_CHARS]
    item.summary_generated_at = generated_at or datetime.now(UTC)
