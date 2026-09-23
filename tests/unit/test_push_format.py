"""推送格式单测（Plan #7）：飞书/钉钉加签与 payload 形状，MockTransport 捕获断言。"""

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

import httpx
from app.db.models import PushChannel
from app.services.push import _build_payload, _sign


def _capture_client(captured: list[httpx.Request], status: int = 200, body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=body or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _channel(ctype: str, url="https://example.com/hook", secret=None) -> PushChannel:
    return PushChannel(
        name="测试渠道",
        channel_type=ctype,
        config_json={"url": url, "secret": secret or ""},
        enabled=True,
    )


def test_feishu_sign_formula():
    from app.services.push import _sign

    secret = "s3cret"
    ts = 1770000000
    expected = base64.b64encode(
        hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()
    assert _sign(secret, ts) == expected


def test_dingtalk_sign_in_query():
    ch = _channel("dingtalk", secret="sec123")
    url, _body = _build_payload(ch, [])
    parsed = urlparse(url)
    assert parsed.path == "/hook" or url.startswith("https://example.com")
    qs = parse_qs(url.split("?", 1)[1])
    assert "timestamp" in qs and "sign" in qs
    expected = base64.b64encode(
        hmac.new(
            b"sec123", f"{qs['timestamp'][0]}\nsec123".encode(), hashlib.sha256
        ).digest()
    ).decode()
    assert qs["sign"][0] == expected  # parse_qs 已做 URL 解码


def test_payload_shapes():
    class FakeVP:
        id = 1
        claim = "黄金看涨" * 30  # 超 60 字截断
        stance = "bullish"
        horizon = "1-3M"
        entity_raw = "黄金"
        confidence = 0.82
        as_of_date = None
        updated_at = None

    rows = [(FakeVP(), "李一恩", None)]

    _u, feishu_body = _build_payload(_channel("feishu"), rows)
    assert feishu_body["msg_type"] == "text"
    text = feishu_body["content"]["text"]
    assert "李一恩 · 黄金 · 看多" in text and "…" in text

    _u, ding_body = _build_payload(_channel("dingtalk"), rows)
    assert ding_body["msgtype"] == "markdown" and ding_body["markdown"]["title"]

    _u, wh_body = _build_payload(_channel("generic_webhook"), rows)
    assert wh_body["event"] == "viewpoints.confirmed"
    assert wh_body["items"][0]["creator_name"] == "李一恩"
    assert wh_body["items"][0]["stance"] == "bullish"

    _u, test_body = _build_payload(_channel("generic_webhook"), [], test=True)
    assert test_body["event"] == "test" and test_body["items"] == []
