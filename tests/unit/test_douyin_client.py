"""DouyinApiClient 单测（Task 2 Step 1）：路径/参数/Header 正确 + 异常映射（DX R3）。"""

import httpx
import pytest
from app.services.media.adapters.douyin_client import DouyinApiClient
from app.services.media.contracts import AdapterProcessError, AdapterTimeoutError

BASE = "http://dtk.test:8080"
KEY = "dtk_test_key"


def make_client(handler) -> DouyinApiClient:
    transport = httpx.MockTransport(handler)
    client = DouyinApiClient(base_url=BASE, api_key=KEY, timeout_sec=5)
    client._client = httpx.Client(
        base_url=BASE, headers={"X-API-Key": KEY}, timeout=5, transport=transport
    )
    return client


def test_fetch_one_video_builds_expected_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"success": True, "data": {"content_id": "1"}})

    data = make_client(handler).fetch_one_video("7686120894896327976")
    assert data == {"content_id": "1"}
    assert seen["url"] == (
        f"{BASE}/api/v1/douyin/video?aweme_id=7686120894896327976&wait=30"
    )
    assert seen["key"] == KEY


def test_fetch_user_posts_builds_expected_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"success": True, "data": {"items": [], "cursor": 1, "has_more": False}},
        )

    data = make_client(handler).fetch_user_posts("MS4wLjABxxx", max_cursor=42, count=5)
    assert data["has_more"] is False
    assert "sec_user_id=MS4wLjABxxx" in seen["url"]
    assert "cursor=42" in seen["url"]
    assert "count=5" in seen["url"]


def test_upstream_5xx_carries_status_and_truncated_body() -> None:
    body = "x" * 500

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=body)

    with pytest.raises(AdapterProcessError) as exc_info:
        make_client(handler).fetch_one_video("1")
    msg = str(exc_info.value)
    assert "HTTP 500" in msg
    # 截断 ≤200 字符（进 last_error 供排障），不全量塞入
    assert "x" * 201 not in msg


def test_202_pending_maps_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202, json={"success": True, "data": {"task_id": "t", "state": "running"}}
        )

    with pytest.raises(AdapterTimeoutError):
        make_client(handler).fetch_one_video("1")


def test_client_timeout_maps_to_adapter_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    with pytest.raises(AdapterTimeoutError):
        make_client(handler).fetch_one_video("1")


def test_failure_envelope_maps_to_process_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "error": {"code": "NOT_FOUND", "message": "The requested resource does not exist."},
            },
        )

    with pytest.raises(AdapterProcessError) as exc_info:
        make_client(handler).fetch_one_video("1")
    assert "NOT_FOUND" in str(exc_info.value)
