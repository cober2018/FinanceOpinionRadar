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


# --- 多模型档案 + 阶段分配（2026-09-30）---


def test_legacy_config_migrates_to_profile(db_session):
    """旧单配置（无 profiles 键）→ 自动生成「主模型」档案，各阶段默认指向它。"""
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
    profiles = llm_config.get_llm_profiles(db_session)
    assert [p["name"] for p in profiles] == ["主模型"]
    assert profiles[0]["key_set"] is True and profiles[0]["api_key"] == ""  # 打码
    assignment = llm_config.get_llm_assignment(db_session)
    assert all(assignment[k] == "legacy" for k, _ in llm_config.STAGES)
    provider = llm_config.build_llm_provider(db_session, task="summary")
    assert type(provider).__name__ == "OpenAICompatProvider"


def test_legacy_review_model_migrates_to_second_profile(db_session):
    """EPIC-05 的 review_model 补丁 → 迁移成第二个档案「复核模型」，review 指向它。"""
    llm_config.put_llm_settings(
        db_session,
        {
            "template": "minimax",
            "base_url": "https://api.minimax.chat/v1",
            "api_key": "mm-key",
            "model": "MiniMax-M3",
            "chat_path": "/text/chatcompletion_v2",
            "review_model": "MiniMax-Text-01",
        },
    )
    profiles = llm_config.get_llm_profiles(db_session, include_secrets=True)
    assert [p["name"] for p in profiles] == ["主模型", "复核模型"]
    assignment = llm_config.get_llm_assignment(db_session)
    assert assignment["review"] == "legacy-review"
    assert assignment["extraction"] == "legacy"


def test_profiles_crud_and_stage_routing(db_session):
    """两个档案 + assignment：各阶段各用各的 provider；key 留空沿用。"""
    llm_config.put_llm_settings(
        db_session,
        {
            "template": "minimax",
            "base_url": "https://api.minimax.chat/v1",
            "api_key": "mm-key",
            "model": "MiniMax-M3",
            "chat_path": "/text/chatcompletion_v2",
        },
    )
    saved = llm_config.put_llm_profiles(
        db_session,
        [
            {"id": "legacy", "name": "抽取主力", "template": "minimax", "base_url": "https://api.minimax.chat/v1",
             "api_key": "", "model": "MiniMax-M3", "chat_path": "/text/chatcompletion_v2"},
            {"name": "复核判断", "template": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
             "api_key": "glm-key", "model": "glm-4-plus", "chat_path": "/chat/completions"},
        ],
    )
    # 第二个档案 id 自动生成；key 打码返回
    assert [p["id"] for p in saved] == ["legacy", saved[1]["id"]]
    assert saved[1]["key_set"] is True and saved[0]["api_key"] == ""

    llm_config.put_llm_assignment(
        db_session, {"extraction": "legacy", "review": saved[1]["id"]}
    )
    ex = llm_config.build_llm_provider(db_session, task="extraction")
    rv = llm_config.build_llm_provider(db_session, task="review")
    su = llm_config.build_llm_provider(db_session, task="summary")
    assert type(ex).__name__ == "OpenAICompatProvider" and ex._chat_path == "/text/chatcompletion_v2"
    assert rv._chat_path == "/chat/completions"  # 复核走 GLM 档案
    assert su._chat_path == "/text/chatcompletion_v2"  # summary 未分配 → 回落第一个档案

    # key 沿用验证：legacy 的 api_key 留空提交后仍能构造出带 key 的 provider
    assert ex._key == "mm-key" and rv._key == "glm-key"


def test_assignment_validation(db_session):
    """未知阶段/未知档案 422；删除档案后 assignment 指向失效回落第一个。"""
    llm_config.put_llm_settings(
        db_session,
        {"template": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
         "api_key": "k", "model": "glm-4-plus"},
    )
    import pytest as _pytest

    with _pytest.raises(ValueError, match="未知阶段"):
        llm_config.put_llm_assignment(db_session, {"nope": "legacy"})
    with _pytest.raises(ValueError, match="不存在"):
        llm_config.put_llm_assignment(db_session, {"review": "ghost"})

    saved = llm_config.put_llm_profiles(
        db_session,
        [{"name": "A", "template": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
          "api_key": "k", "model": "glm-4-plus", "chat_path": ""}],
    )
    llm_config.put_llm_assignment(db_session, {"extraction": saved[0]["id"]})
    llm_config.put_llm_profiles(db_session, [])  # 清空档案
    assert llm_config.get_llm_assignment(db_session) == {
        k: None for k, _ in llm_config.STAGES
    }
    assert type(llm_config.build_llm_provider(db_session, task="extraction")).__name__ == "OpenAICompatProvider"
