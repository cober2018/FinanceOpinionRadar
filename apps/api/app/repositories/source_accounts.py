from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import SourceAccount
from app.repositories.base import BaseRepository


class SourceAccountRepository(BaseRepository[SourceAccount]):
    model = SourceAccount

    def upsert_by_external(
        self,
        *,
        creator_id: int,
        platform: str,
        external_id: str,
        handle: str | None = None,
        url: str | None = None,
        discovery_mode: str = "manual",
        enabled: bool = True,
    ) -> SourceAccount:
        """按 (platform, external_id) 幂等 upsert（D39）：discover 重复/并发回调不产生重复行。

        ON CONFLICT DO UPDATE 由 PG 唯一索引串行化并发插入；
        creator_id 视为不可变归属，冲突时不更新。
        """
        stmt = pg_insert(SourceAccount).values(
            creator_id=creator_id,
            platform=platform,
            external_id=external_id,
            handle=handle,
            url=url,
            discovery_mode=discovery_mode,
            enabled=enabled,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["platform", "external_id"],
            set_={
                "handle": stmt.excluded.handle,
                "url": stmt.excluded.url,
                "discovery_mode": stmt.excluded.discovery_mode,
                "enabled": stmt.excluded.enabled,
            },
        )
        self.session.execute(stmt)
        # upsert 后行必然存在；不存在即为不变量破坏，应显式抛错而非返回 None
        return self.session.scalars(
            select(SourceAccount).where(
                SourceAccount.platform == platform,
                SourceAccount.external_id == external_id,
            )
        ).one()
