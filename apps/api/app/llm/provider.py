"""LLM Provider（RAD-042）：OpenAI 兼容 generate_json + Mock 兜底。

接口（执行计划 §7）：generate_json(system_prompt, user_prompt, schema, model, temperature)
→ LLMResponse(data, usage, provider, model)。实现 timeout / 指数重试 / usage 记录；
JSON 解析失败与 5xx 都走重试；重试耗尽抛 LLMError。
LLM_API_KEY 为空时工厂返回 Mock（合法空抽取），dev 无钥可跑通全链。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)


class LLMError(Exception):
    pass


class LLMResponseParseError(LLMError):
    def __init__(self, message: str, raw_content: str, provider: str, model: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content
        self.provider = provider
        self.model = model


@dataclass(frozen=True)
class LLMResponse:
    data: dict[str, Any]
    usage: dict[str, int]
    provider: str
    model: str
    raw_content: str = ""
    raw_head: str = ""  # 原始 content 头部（空返回诊断用）


class LLMProvider(Protocol):
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse: ...


@dataclass
class MockLLMProvider:
    """无钥开发/测试用：返回 schema 形状的空抽取（viewpoints: []）。"""

    calls: list = field(default_factory=list)

    def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return LLMResponse(
            data={"viewpoints": []},
            usage={"total_tokens": 0},
            provider="mock",
            model="mock",
            raw_content='{"viewpoints": []}',
        )


def _extract_json(content: str) -> dict:
    """健壮 JSON 提取：剥 markdown 围栏；兜底截取首个 { 到最后一个 }。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lo, hi = text.find("{"), text.rfind("}")
        if lo >= 0 and hi > lo:
            return json.loads(text[lo : hi + 1])
        raise


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: int = 120,
        max_retries: int = 2,
        chat_path: str = "/chat/completions",
        max_tokens: int = 16384,
    ) -> None:
        if not base_url or not api_key:
            raise LLMError(
                "OpenAICompatProvider 需要 base_url 与 api_key（LLM_BASE_URL/LLM_API_KEY）"
            )
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._timeout = timeout_sec
        self._max_retries = max_retries
        # 个别服务路径不同（MiniMax: /text/chatcompletion_v2）
        self._chat_path = chat_path or "/chat/completions"
        # 推理模型（M3/R 系）的推理与回答共用 token 预算，默认值小会被截断成空 content
        self._max_tokens = max_tokens

    def _post(self, payload: dict) -> httpx.Response:
        return httpx.post(
            f"{self._base}{self._chat_path}",
            headers={"Authorization": f"Bearer {self._key}"},
            json=payload,
            timeout=self._timeout,
        )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload = {
            "model": model or self._model,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        last_err: str = ""
        last_error_kind = "call"
        last_raw_content = ""
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._post(payload)
            except httpx.HTTPError as exc:
                last_err = f"请求异常: {exc}"
                last_error_kind = "call"
                logger.warning("llm_request_retry", attempt=attempt, error=last_err)
                time.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                last_error_kind = "call"
                logger.warning("llm_request_retry", attempt=attempt, error=last_err)
                time.sleep(2**attempt)
                continue
            content = ""
            try:
                body = resp.json()
                message = body["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                usage = body.get("usage") or {}
                data = _extract_json(content)
            except (KeyError, json.JSONDecodeError, ValueError) as exc:
                head = repr(content[:60]) if isinstance(content, str) else "?"
                last_err = f"响应解析失败: {exc}（content 头部={head}）"
                last_error_kind = "parse"
                last_raw_content = content if isinstance(content, str) else ""
                logger.warning("llm_request_retry", attempt=attempt, error=last_err)
                time.sleep(2**attempt)
                continue
            return LLMResponse(
                data=data,
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                provider="openai-compat",
                model=str(payload["model"]),
                raw_content=content,
                raw_head=content[:120],
            )
        message = f"LLM 请求失败（重试 {self._max_retries} 次后）: {last_err}"
        if last_error_kind == "parse":
            raise LLMResponseParseError(
                message,
                last_raw_content,
                provider="openai-compat",
                model=str(payload["model"]),
            )
        raise LLMError(message)
