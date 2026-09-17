"""对象存储入口（RAD-030）：业务代码从这里拿 Storage，禁止直接 import boto3。"""

from functools import lru_cache

from app.core.settings import get_settings
from app.services.storage.base import Storage, StorageError
from app.services.storage.s3 import MinioStorage

__all__ = ["MinioStorage", "Storage", "StorageError", "get_storage"]


@lru_cache
def get_storage() -> MinioStorage:
    s = get_settings()
    return MinioStorage(
        endpoint=s.s3_endpoint_url,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        bucket=s.s3_bucket_media,
    )
