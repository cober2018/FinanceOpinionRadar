"""media_asset 仓储（RAD-034）：记录与列举，不做更新（资产不可变）。"""

from sqlalchemy.orm import Session

from app.db.models import MediaAsset


class MediaAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        source_item_id: int,
        *,
        asset_type: str,
        storage_uri: str,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        duration_ms: int | None = None,
    ) -> MediaAsset:
        asset = MediaAsset(
            source_item_id=source_item_id,
            asset_type=asset_type,
            storage_uri=storage_uri,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            duration_ms=duration_ms,
        )
        self._session.add(asset)
        self._session.flush()
        return asset

    def list_for_item(self, source_item_id: int) -> list[MediaAsset]:
        return list(
            self._session.query(MediaAsset)
            .filter(MediaAsset.source_item_id == source_item_id)
            .order_by(MediaAsset.id)
            .all()
        )
