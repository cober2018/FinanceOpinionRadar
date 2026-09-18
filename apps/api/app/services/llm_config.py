"""LLM 运行时配置（EPIC-04+）：模板预设 + DB 覆盖 + provider 构造。

配置来源优先级：app_setting["llm"]（设置页保存）> env（.env/环境变量）。
api_key 存 DB（内部单机工具，可接受；泄露面=数据库文件本身）。
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import AppSetting
from app.llm.templates import LLM_TEMPLATES

LLM_SETTINGS_KEY = "llm"

_DEFAULTS = {
    "template": "custom",
    "base_url": "",
    "api_key": "",
    "model": "",
    "chat_path": "/chat/completions",
}


@dataclass(frozen=True)
class EffectiveLLM:
    template: str
    base_url: str
    api_key: str
    model: str
    chat_path: str
    timeout_sec: int
    max_retries: int

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


def get_llm_settings(session: Session) -> dict:
    """设置页读取：模板列表 + 当前值 + env 兜底说明。"""
    s = get_settings()
    row = session.get(AppSetting, LLM_SETTINGS_KEY)
    stored = (row.value if row else {}) or {}
    merged = dict(_DEFAULTS)
    merged.update(stored)
    # base_url/api_key/model 为空时回落 env
    env_fallback = {
        "base_url": s.llm_base_url,
        "api_key": s.llm_api_key,
        "model": s.llm_model,
    }
    for k, v in env_fallback.items():
        if not merged.get(k):
            merged[k] = v
    return {
        "templates": [
            {
                "key": t.key,
                "label": t.label,
                "base_url": t.base_url,
                "models": list(t.models),
                "chat_path": t.chat_path,
                "note": t.note,
            }
            for t in LLM_TEMPLATES
        ],
        **merged,
        "effective": {
            "base_url": merged["base_url"],
            "model": merged["model"] or s.llm_model,
            "timeout_sec": s.llm_timeout_sec,
            "max_retries": s.llm_max_retries,
            "key_set": bool(merged["api_key"]),
        },
    }


def put_llm_settings(session: Session, payload: dict) -> dict:
    """校验 + 落库（value 全量替换）。非法值抛 ValueError → 路由层 422。"""
    clean = dict(_DEFAULTS)
    template = payload.get("template") or "custom"
    if template not in {t.key for t in LLM_TEMPLATES}:
        raise ValueError(f"未知模板: {template}")
    clean["template"] = template
    clean["base_url"] = str(payload.get("base_url") or "").strip().rstrip("/")
    clean["api_key"] = str(payload.get("api_key") or "").strip()
    clean["model"] = str(payload.get("model") or "").strip()
    clean["chat_path"] = (
        str(payload.get("chat_path") or "/chat/completions").strip() or "/chat/completions"
    )
    if clean["base_url"] and not clean["base_url"].startswith(("http://", "https://")):
        raise ValueError("base_url 需以 http(s):// 开头")
    if clean["chat_path"] and not clean["chat_path"].startswith("/"):
        raise ValueError("chat_path 需以 / 开头")
    row = session.get(AppSetting, LLM_SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=LLM_SETTINGS_KEY, value=clean)
        session.add(row)
    else:
        row.value = clean
    session.commit()
    return get_llm_settings(session)


def build_llm_provider(session: Session):
    """按生效配置构造 provider；未配置完整 → Mock（链路通、产出空）。"""
    from app.llm.provider import MockLLMProvider, OpenAICompatProvider

    s = get_settings()
    cfg = get_llm_settings(session)
    model = cfg["model"] or s.llm_model
    if not (cfg["base_url"] and cfg["api_key"]):
        return MockLLMProvider()
    return OpenAICompatProvider(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=model,
        timeout_sec=s.llm_timeout_sec,
        max_retries=s.llm_max_retries,
        chat_path=cfg["chat_path"],
    )
