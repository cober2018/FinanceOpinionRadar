"""对象存储契约（RAD-030）：业务代码只许用此接口，禁止直接调用 boto3。"""

from pathlib import Path
from typing import Protocol


class StorageError(Exception):
    """存储层错误统一包装（网络/权限/桶不存在等）。"""


class Storage(Protocol):
    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        """上传本地文件，返回 storage_uri（s3://<bucket>/<key>）。"""
        ...

    def get_signed_url(self, key: str, *, expires_sec: int = 3600) -> str: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...
