"""LLM 外呼并发闸接线测试：占闸/禁用/排队超时转 LLMError。

信号量本体（Redis ZSET 原子抢位/TTL 回收/fail-open）已由 test_download_gate.py
覆盖，此处只测 OpenAICompatProvider._post 的接线。
"""

from unittest.mock import MagicMock, patch

import pytest

from app.llm.provider import LLMError, OpenAICompatProvider
from app.services.download_gate import EgressBusyError


def _provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="http://llm.test/v1", api_key="k", model="m", max_tokens=16
    )


def test_post_skips_gate_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.llm.provider.get_settings", lambda: MagicMock(llm_concurrency=0)
    )
    gate = MagicMock()
    with (
        patch("app.llm.provider.get_download_gate", return_value=gate),
        patch.object(
            OpenAICompatProvider, "_post_once", return_value=MagicMock(status_code=200)
        ) as post_once,
    ):
        resp = _provider()._post({})
    gate.slot.assert_not_called()
    assert post_once.call_count == 1
    assert resp.status_code == 200


def test_post_acquires_slot_with_configured_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.llm.provider.get_settings", lambda: MagicMock(llm_concurrency=3)
    )
    gate = MagicMock()
    with (
        patch("app.llm.provider.get_download_gate", return_value=gate),
        patch.object(
            OpenAICompatProvider, "_post_once", return_value=MagicMock(status_code=200)
        ) as post_once,
    ):
        _provider()._post({})
    gate.slot.assert_called_once_with(
        "llm", limit=3, ttl_sec=900, timeout_sec=600.0
    )
    assert post_once.call_count == 1


def test_post_wait_timeout_raises_llm_error(monkeypatch) -> None:
    """排队超时必须转 LLMError——上层重试/兜底按既有语义接管，异常类型不裸穿。"""
    monkeypatch.setattr(
        "app.llm.provider.get_settings", lambda: MagicMock(llm_concurrency=3)
    )
    gate = MagicMock()
    gate.slot.side_effect = EgressBusyError("llm 满载")
    with (
        patch("app.llm.provider.get_download_gate", return_value=gate),
        patch.object(OpenAICompatProvider, "_post_once") as post_once,
        pytest.raises(LLMError, match="排队超时"),
    ):
        _provider()._post({})
    post_once.assert_not_called()
