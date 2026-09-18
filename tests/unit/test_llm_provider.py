"""LLM Provider 单测：OpenAI 兼容实现的 JSON 解析/重试/超时/usage（httpx 打桩）。"""

import json

import httpx
import pytest
from app.llm.provider import (
    LLMError,
    LLMResponse,
    MockLLMProvider,
    OpenAICompatProvider,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "viewpoints": {
            "type": "array",
            "items": {"type": "object", "properties": {"claim": {"type": "string"}}},
        }
    },
}


def _provider(calls: list, responses: list[httpx.Response], **kw) -> OpenAICompatProvider:
    def fake_post(payload):
        calls.append(payload)
        return responses.pop(0)

    defaults = {
        "base_url": "http://llm.local/v1",
        "api_key": "sk-test",
        "model": "test-model",
        "timeout_sec": 5,
        "max_retries": 2,
    }
    defaults.update(kw)
    p = OpenAICompatProvider(**defaults)
    monkey_patch_httpx(p, fake_post)
    return p


def monkey_patch_httpx(p: OpenAICompatProvider, fake_post):
    p._post = fake_post


def _resp(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "http://llm.local"))


def test_generate_json_returns_content_and_usage():
    calls: list = []
    body = {
        "choices": [{"message": {"content": json.dumps({"viewpoints": [{"claim": "降息利好"}]})}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    p = _provider(calls, [_resp(body)])
    out = p.generate_json("sys", "user", SCHEMA, temperature=0.2)
    assert isinstance(out, LLMResponse)
    assert out.data["viewpoints"][0]["claim"] == "降息利好"
    assert out.usage["total_tokens"] == 120
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_generate_json_retries_on_5xx_then_succeeds():
    calls: list = []
    body = {"choices": [{"message": {"content": '{"viewpoints": []}'}}], "usage": {}}
    p = _provider(calls, [_resp({}, status=503), _resp(body)])
    monkeypatch_sleep(p)
    out = p.generate_json("s", "u", SCHEMA)
    assert out.data == {"viewpoints": []}
    assert len(calls) == 2  # 重试 1 次


def test_generate_json_raises_after_retries_exhausted():
    calls: list = []
    p = _provider(calls, [_resp({}, status=500)] * 3)
    monkeypatch_sleep(p)
    with pytest.raises(LLMError, match="HTTP 500"):
        p.generate_json("s", "u", SCHEMA)


def test_generate_json_raises_on_invalid_json():
    calls: list = []
    body = {"choices": [{"message": {"content": "不是 JSON"}}], "usage": {}}
    p = _provider(calls, [_resp(body)] * 3)  # 每次重试都要有响应
    monkeypatch_sleep(p)
    with pytest.raises(LLMError, match="JSON"):
        p.generate_json("s", "u", SCHEMA)


def test_mock_provider_returns_schema_shaped_empty():
    p = MockLLMProvider()
    out = p.generate_json("s", "u", SCHEMA)
    assert out.data == {"viewpoints": []}
    assert out.provider == "mock"


def monkeypatch_sleep(p):
    import app.llm.provider as mod

    mod.time.sleep = lambda s: None
