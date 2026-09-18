"""live_chat_message 仓储（Plan #5）：弹幕批量幂等写入。"""

from sqlalchemy import select

from app.db.models import LiveChatMessage
from app.services.danmaku.parse import DanmakuRecord


class LiveChatMessageRepository:
    def __init__(self, session) -> None:
        self.session = session

    def insert_many(
        self, item_id: int, records: list[DanmakuRecord], *, platform: str = "douyin"
    ) -> int:
        """批内查重 → 插入新行；返回实际插入数。

        幂等三层（F5）：批内 setdefault → 已存在 msg_id 预查跳过 → 唯一约束兜底并发
        （ingest 任务本身有全局单飞闸，约束只作最后防线）。
        """
        unique: dict[str, DanmakuRecord] = {}
        for r in records:
            unique.setdefault(r.external_msg_id, r)
        existing = self._existing_ids(item_id, set(unique))
        inserted = 0
        for msg_id, r in unique.items():
            if msg_id in existing:
                continue
            self.session.add(
                LiveChatMessage(
                    source_item_id=item_id,
                    platform=platform,
                    msg_type=r.msg_type,
                    external_msg_id=r.external_msg_id,
                    user_id=r.user_id,
                    user_name=r.user_name,
                    text=r.text,
                    gift_name=r.gift_name,
                    repeat_count=r.repeat_count,
                    like_count=r.like_count,
                    member_count=r.member_count,
                    published_at=r.published_at,
                )
            )
            inserted += 1
        self.session.flush()
        return inserted

    def _existing_ids(self, item_id: int, msg_ids: set[str]) -> set[str]:
        if not msg_ids:
            return set()
        rows = self.session.execute(
            select(LiveChatMessage.external_msg_id).where(
                LiveChatMessage.source_item_id == item_id,
                LiveChatMessage.external_msg_id.in_(msg_ids),
            )
        )
        return {row[0] for row in rows}


__all__ = ["LiveChatMessageRepository"]
