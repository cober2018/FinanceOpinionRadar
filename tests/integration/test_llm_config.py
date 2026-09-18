"""LLM 配置集成测试（PG）：模板覆盖/校验/provider 构造（Mock 回落与 OpenAI 路径差异）。"""

import pytest
from app.llm.templates import get_template
from app.services import llm_config


def test_templates_cover_domestic_providers():
    keys = {t.key for t in llm_config.LLM_TEMPLATES}
    assert {"deepseek", "glm", "minimax", "kimi", "qwen", "custom"} <= keys
    mm = get_template("minimax")
    assert mm is not None and "chatcompletion_v2" in mm.chat_path


def test_put_validates_base_url_and_template(db_session):
    with pytest.raises(ValueError, match="http"):
        llm_config.put_llm_settings(db_session, {"template": "deepseek", "base_url": "api.deepseek.com"})
    with pytest.raises(ValueError, match="未知模板"):
        llm_config.put_llm_settings(db_session, {"template": "nope"})


def test_put_persists_and_provider_builds_mock_when_incomplete(db_session):
    llm_config.put_llm_settings(
        db_session,
        {
            "template": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
        },
    )
    cfg = llm_config.get_llm_settings(db_session)
    assert cfg["template"] == "deepseek" and cfg["model"] == "deepseek-chat"
    provider = llm_config.build_llm_provider(db_session)
    assert type(provider).__name__ == "MockLLMProvider"  # key 空 → Mock


def test_put_full_config_builds_openai_provider(db_session):
    llm_config.put_llm_settings(
        db_session,
        {
            "template": "minimax",
            "base_url": "https://api.minimax.chat/v1",
            "api_key": "mm-key",
            "model": "MiniMax-Text-01",
            "chat_path": "/text/chatcompletion_v2",
        },
    )
    provider = llm_config.build_llm_provider(db_session)
    assert type(provider).__name__ == "OpenAICompatProvider"
    assert provider._chat_path == "/text/chatcompletion_v2"
