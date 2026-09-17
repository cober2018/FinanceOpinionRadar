"""账号管理 API 校验层单测（Plan #4 Task 3 Step 3）：无需 DB 的 4xx 行为。

DB 依赖被 get_db 惰性求值隔离：校验失败发生在任何查询之前。
CRUD 全链路见 tests/integration/test_source_accounts_api.py。
"""

import pytest
from app.api.v1.source_accounts import CreateSourceAccountRequest
from app.main import app
from fastapi.testclient import TestClient
from pydantic import ValidationError

client = TestClient(app)


def test_discovery_mode_literal_rejects_unknown_422() -> None:
    with pytest.raises(ValidationError):
        CreateSourceAccountRequest(
            platform="youtube", url="https://www.youtube.com/@a", discovery_mode="aggressive"
        )


def test_post_rejects_non_allowlisted_url_400() -> None:
    resp = client.post(
        "/api/v1/source-accounts",
        json={"platform": "douyin", "url": "https://evil.example.com/user/MS4wLjABx"},
    )
    assert resp.status_code == 400
    assert "白名单" in resp.json()["detail"]


def test_post_douyin_without_sec_uid_422() -> None:
    resp = client.post(
        "/api/v1/source-accounts",
        json={"platform": "douyin", "url": "https://www.douyin.com/search/abc"},
    )
    assert resp.status_code == 422
    assert "sec_uid" in resp.json()["detail"]


def test_post_douyin_user_url_derives_sec_uid_body_ok_without_db() -> None:
    # sec_uid 可从 URL 推导时，请求体校验通过（此后才触 DB，单测不覆盖）
    body = CreateSourceAccountRequest(
        platform="douyin", url="https://www.douyin.com/user/MS4wLjABdytest"
    )
    assert str(body.url) == "https://www.douyin.com/user/MS4wLjABdytest"
