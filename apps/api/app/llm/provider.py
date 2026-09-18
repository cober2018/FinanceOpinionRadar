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


@dataclass(frozen=True)
class LLMResponse:
    data: dict[str, Any]
    usage: dict[str, int]
    provider: str
    model: str


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
            data={"viewpoints": []}, usage={"total_tokens": 0}, provider="mock", model="mock"
        )


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
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        last_err: str = ""
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._post(payload)
            except httpx.HTTPError as exc:
                last_err = f"请求异常: {exc}"
                logger.warning("llm_request_retry", attempt=attempt, error=last_err)
                time.sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("llm_request_retry", attempt=attempt, error=last_err)
                time.sleep(2**attempt)
                continue
            try:
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                usage = body.get("usage") or {}
                data = json.loads(content)
            except (KeyError, json.JSONDecodeError) as exc:
                last_err = f"响应 JSON 解析失败: {exc}"
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
            )
        raise LLMError(f"LLM 请求失败（重试 {self._max_retries} 次后）: {last_err}")
