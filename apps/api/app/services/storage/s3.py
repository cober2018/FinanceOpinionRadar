"""MinIO/S3 实现（RAD-030）：path-style 寻址 + 惰性 ensure bucket。"""

from functools import partial
from pathlib import Path

import boto3
import botocore.exceptions
from botocore.config import Config

from app.services.storage.base import StorageError


class MinioStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        _client=None,  # 测试注入
    ) -> None:
        self._bucket = bucket
        self._client = _client
        self._new_client = partial(
            boto3.client,
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 2},
            ),
        )
        self._bucket_ready = False

    def _s3(self):
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            self._s3().head_bucket(Bucket=self._bucket)
        except botocore.exceptions.ClientError:
            self._s3().create_bucket(Bucket=self._bucket)
        except botocore.exceptions.BotoCoreError as exc:
            raise StorageError(f"存储不可达: {exc}") from exc
        self._bucket_ready = True

    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        self._ensure_bucket()
        extra = {"ContentType": content_type} if content_type else None
        try:
            self._s3().upload_file(str(path), self._bucket, key, ExtraArgs=extra)
        except (botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(f"上传失败 {key}: {exc}") from exc
        return f"s3://{self._bucket}/{key}"

    def get_signed_url(self, key: str, *, expires_sec: int = 3600) -> str:
        self._ensure_bucket()
        try:
            return self._s3().generate_presigned_url(
                "get_object",
                {"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_sec,
            )
        except botocore.exceptions.BotoCoreError as exc:
            raise StorageError(f"签名失败 {key}: {exc}") from exc

    def exists(self, key: str) -> bool:
        self._ensure_bucket()
        try:
            self._s3().head_object(Bucket=self._bucket, Key=key)
            return True
        except botocore.exceptions.ClientError:
            return False

    def delete(self, key: str) -> None:
        self._ensure_bucket()
        try:
            self._s3().delete_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.BotoCoreError as exc:
            raise StorageError(f"删除失败 {key}: {exc}") from exc
