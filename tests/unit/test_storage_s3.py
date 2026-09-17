"""MinioStorage 单测：注入假 botocore client，不碰网络。"""

import botocore.exceptions
import pytest
from app.services.storage.s3 import MinioStorage, StorageError


class FakeClient:
    def __init__(self, head_error=None):
        self.calls = []
        self._head_error = head_error

    def head_bucket(self, Bucket):
        self.calls.append(("head_bucket", Bucket))
        if self._head_error:
            raise self._head_error

    def create_bucket(self, Bucket):
        self.calls.append(("create_bucket", Bucket))

    def upload_file(self, Filename, Bucket, Key, **kw):
        self.calls.append(("upload_file", Key))

    def head_object(self, Bucket, Key):
        self.calls.append(("head_object", Key))
        if Key == "missing":
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "404"}}, "HeadObject"
            )

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        return f"https://sig/{Params['Key']}"

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete_object", Key))


def make(client):
    return MinioStorage(
        endpoint="http://localhost:9000",
        access_key="k",
        secret_key="s",
        bucket="b",
        _client=client,
    )


def test_bucket_ensured_once_then_reused(tmp_path):
    c = FakeClient()
    s = make(c)
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")
    s.put_file("a/b.bin", f)
    s.put_file("c.bin", f)
    assert c.calls.count(("head_bucket", "b")) == 1
    assert c.calls.count(("create_bucket", "b")) == 0


def test_bucket_created_when_missing(tmp_path):
    c = FakeClient(
        head_error=botocore.exceptions.ClientError(
            {"Error": {"Code": "404"}}, "HeadBucket"
        )
    )
    s = make(c)
    s.put_file("k", tmp_path / "a.bin")
    assert ("create_bucket", "b") in c.calls


def test_put_returns_s3_uri(tmp_path):
    s = make(FakeClient())
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")
    assert s.put_file("sub/a", f) == "s3://b/sub/a"


def test_exists_true_false():
    s = make(FakeClient())
    assert s.exists("k") is True
    assert s.exists("missing") is False


def test_signed_url_and_delete():
    s = make(FakeClient())
    assert s.get_signed_url("k", expires_sec=60) == "https://sig/k"
    s.delete("k")  # 不抛


def test_boto3_errors_wrapped(tmp_path):
    class Boom(FakeClient):
        def upload_file(self, *a, **k):
            raise botocore.exceptions.BotoCoreError()

    s = make(Boom())
    with pytest.raises(StorageError):
        s.put_file("k", tmp_path / "a.bin")
