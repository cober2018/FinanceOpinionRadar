"""MinIO 真实例 roundtrip（RAD-030）：不可达即跳过，桶用 -test 后缀隔离。"""

import socket

import pytest
from app.core.settings import get_settings
from app.services.storage.s3 import MinioStorage


@pytest.fixture(scope="module")
def storage() -> MinioStorage:
    s = get_settings()
    host = s.s3_endpoint_url.split("//")[1].split(":")[0]
    port = int(s.s3_endpoint_url.rsplit(":", 1)[1])
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex((host, port)) != 0:
            pytest.skip(f"MinIO 不可达 {s.s3_endpoint_url}（先 make bootstrap）")
    return MinioStorage(
        endpoint=s.s3_endpoint_url,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        bucket=f"{s.s3_bucket_media}-test",
    )


def test_roundtrip(tmp_path, storage: MinioStorage) -> None:
    key = f"it/{tmp_path.name}/a.txt"
    f = tmp_path / "a.txt"
    f.write_text("hello")
    uri = storage.put_file(key, f)
    assert uri.startswith("s3://")
    assert storage.exists(key)
    assert "a.txt" in storage.get_signed_url(key)
    storage.delete(key)
    assert not storage.exists(key)
