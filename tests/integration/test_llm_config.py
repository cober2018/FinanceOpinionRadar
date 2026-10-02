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
    assert {k: v for k, v in llm_config.get_llm_assignment(db_session).items() if k in {k for k, _ in llm_config.STAGES}} == {
        k: None for k, _ in llm_config.STAGES
    }
    assert type(llm_config.build_llm_provider(db_session, task="extraction")).__name__ == "OpenAICompatProvider"


# --- 兜底模型（2026-10-02：主模型坏掉自动切兜底）---


def test_assignment_fallback_key_roundtrip(db_session):
    """assignment 接受 fallback 键；指向失效回落 None；未知阶段仍 422。"""
    llm_config.put_llm_settings(
        db_session,
        {"template": "minimax", "base_url": "https://api.minimax.chat/v1",
         "api_key": "mm", "model": "MiniMax-M3"},
    )
    saved = llm_config.put_llm_profiles(
        db_session,
        [{"name": "备胎", "template": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
          "api_key": "glm", "model": "glm-4-plus", "chat_path": "/chat/completions"}],
    )
    out = llm_config.put_llm_assignment(
        db_session, {"extraction": saved[0]["id"], "fallback": saved[0]["id"]}
    )
    assert out["fallback"] == saved[0]["id"]
    # 指向失效（档案随后被删）→ 读时回落 None（PUT 时严格 422）
    llm_config.put_llm_profiles(db_session, [])
    assert llm_config.get_llm_assignment(db_session)["fallback"] is None
    import pytest as _pytest
    with _pytest.raises(ValueError, match="未知阶段"):
        llm_config.put_llm_assignment(db_session, {"nope": "legacy"})


def test_build_wraps_fallback_provider(db_session):
    """配置兜底且 ≠ 主档案 → FallbackLLMProvider；同档案/未配置 → 裸 provider。"""
    llm_config.put_llm_settings(
        db_session,
        {"template": "minimax", "base_url": "https://api.minimax.chat/v1",
         "api_key": "mm", "model": "MiniMax-M3"},
    )
    saved = llm_config.put_llm_profiles(
        db_session,
        [
            {"id": "main", "name": "主力", "template": "minimax", "base_url": "https://api.minimax.chat/v1",
             "api_key": "mm-key", "model": "MiniMax-M3", "chat_path": "/text/chatcompletion_v2"},
            {"name": "备胎", "template": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
             "api_key": "glm", "model": "glm-4-plus", "chat_path": "/chat/completions"},
        ],
    )
    from app.llm.provider import FallbackLLMProvider, OpenAICompatProvider

    llm_config.put_llm_assignment(
        db_session, {"summary": "main", "fallback": saved[1]["id"]}
    )
    p = llm_config.build_llm_provider(db_session, task="summary")
    assert type(p).__name__ == "FallbackLLMProvider"
    assert p._fallback._chat_path == "/chat/completions"  # 兜底走 GLM 档案

    # 兜底 = 主模型同档案 → 不包装
    llm_config.put_llm_assignment(db_session, {"summary": "main", "fallback": "main"})
    assert type(llm_config.build_llm_provider(db_session, task="summary")).__name__ == "OpenAICompatProvider"

    # 未配置兜底 → 裸 provider
    llm_config.put_llm_assignment(db_session, {"summary": "main"})
    assert type(llm_config.build_llm_provider(db_session, task="summary")).__name__ == "OpenAICompatProvider"


def test_fallback_engages_on_primary_failure(db_session):
    """主模型重试耗尽抛 LLMError → 兜底接管重跑；主模型正常不触发。"""
    from app.llm.provider import FallbackLLMProvider, LLMError, LLMResponse

    class FlakyPrimary:
        calls = 0

        def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
            FlakyPrimary.calls += 1
            raise LLMError("LLM 请求失败（重试 2 次后）: HTTP 500")

    class HealthyFallback:
        calls = 0

        def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
            HealthyFallback.calls += 1
            return LLMResponse(data={"ok": True}, usage={}, provider="fb", model="fallback")

    p = FallbackLLMProvider(FlakyPrimary(), HealthyFallback(), task="summary")
    out = p.generate_json("sys", "user", {})
    assert out.data == {"ok": True} and FlakyPrimary.calls == 1 and HealthyFallback.calls == 1

    class GoodPrimary:
        calls = 0

        def generate_json(self, system_prompt, user_prompt, schema, *, model=None, temperature=0.2):
            GoodPrimary.calls += 1
            return LLMResponse(data={"ok": 1}, usage={}, provider="p", model="primary")

    p2 = FallbackLLMProvider(GoodPrimary(), HealthyFallback(), task="extraction")
    p2.generate_json("sys", "user", {})
    assert GoodPrimary.calls == 1 and HealthyFallback.calls == 1  # 兜底未被触发（仍是 1 次历史调用）
