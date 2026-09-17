from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import SourceItem
from app.repositories.base import BaseRepository

# 冲突时刷新的字段（标题/时长/封面等平台可变元数据）；status/创建信息不回写
_REFRESH_FIELDS = ("title", "duration_ms", "thumbnail_url", "canonical_url", "published_at")
# 可空enrichment字段：上游缺席（None）时保留现值，不得把已解析数据冲成 NULL
_NULLABLE_REFRESH = ("title", "thumbnail_url", "published_at")


class SourceItemRepository(BaseRepository[SourceItem]):
    model = SourceItem

    def upsert_by_external(
        self,
        *,
        source_account_id: int,
        external_item_id: str,
        title: str | None = None,
        description: str | None = None,
        canonical_url: str | None = None,
        thumbnail_url: str | None = None,
        published_at=None,
        duration_ms: int | None = None,
        item_type: str = "vod",
        metadata_json: dict | None = None,
    ) -> tuple[SourceItem, bool]:
        """按 (source_account_id, external_item_id) 幂等 upsert，返回 (行, 是否新建)。

        新建标志供 discover 流程决定是否投递 prepare_source_item（C7）。
        判定走显式 SELECT（P5）；并发插入窗口由 ON CONFLICT DO UPDATE 兜底。
        """
        existing = self._select(account_id=source_account_id, external=external_item_id)
        if existing is not None:
            incoming = {
                "title": title,
                "duration_ms": duration_ms,
                "thumbnail_url": thumbnail_url,
                "canonical_url": canonical_url,
                "published_at": published_at,
            }
            for f, value in incoming.items():
                if value is not None or f not in _NULLABLE_REFRESH:
                    setattr(existing, f, value)
            self.session.flush()
            return existing, False

        stmt = pg_insert(SourceItem).values(
            source_account_id=source_account_id,
            external_item_id=external_item_id,
            title=title,
            description=description,
            canonical_url=canonical_url,
            thumbnail_url=thumbnail_url,
            published_at=published_at,
            duration_ms=duration_ms,
            item_type=item_type,
            status="discovered",
            metadata_json=metadata_json or {},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_account_id", "external_item_id"],
            set_={
                f: (
                    func.coalesce(getattr(stmt.excluded, f), getattr(SourceItem, f))
                    if f in _NULLABLE_REFRESH
                    else getattr(stmt.excluded, f)
                )
                for f in _REFRESH_FIELDS
            },
        )
        self.session.execute(stmt)
        row = self._select(account_id=source_account_id, external=external_item_id)
        if row is None:
            # upsert 后行必然存在；不存在即为不变量破坏，显式抛错
            raise RuntimeError(f"upsert 后行缺失: {source_account_id}/{external_item_id}")
        return row, True

    def list_by_status(self, status: str, *, limit: int) -> list[SourceItem]:
        """注记②补扫：按 id 升序取指定状态条目（limit 截断防单轮过载）。"""
        return list(
            self.session.scalars(
                select(SourceItem)
                .where(SourceItem.status == status)
                .order_by(SourceItem.id)
                .limit(limit)
            )
        )

    def _select(self, *, account_id: int, external: str) -> SourceItem | None:
        return self.session.scalars(
            select(SourceItem).where(
                SourceItem.source_account_id == account_id,
                SourceItem.external_item_id == external,
            )
        ).first()
