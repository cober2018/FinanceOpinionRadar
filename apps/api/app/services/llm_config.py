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
    "review_model": "",  # LLM 复核单独模型；空=沿用主模型（EPIC-05）
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
    clean["review_model"] = str(payload.get("review_model") or "").strip()
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


def build_llm_provider(session: Session, task: str = "extraction"):
    """按阶段构造 provider：llm_assignment 指向的 profile 优先，无则回落旧单配置链。

    task ∈ extraction|review|summary|polish；未配置完整 → Mock（链路通、产出空）。
    """
    from app.llm.provider import MockLLMProvider, OpenAICompatProvider

    s = get_settings()
    profile = _resolve_profile(session, task)
    if profile is not None:
        return OpenAICompatProvider(
            base_url=profile["base_url"],
            api_key=profile["api_key"],
            model=profile["model"],
            timeout_sec=s.llm_timeout_sec,
            max_retries=s.llm_max_retries,
            chat_path=profile["chat_path"] or "/chat/completions",
            max_tokens=s.llm_max_tokens,
        )
    return _legacy_provider(session)


# --- 多模型档案 + 阶段分配（用户 2026-09-30：配多个大模型，各阶段各用各的）---

LLM_PROFILES_KEY = "llm_profiles"
LLM_ASSIGNMENT_KEY = "llm_assignment"

STAGES: tuple[tuple[str, str], ...] = (
    ("extraction", "观点抽取"),
    ("review", "LLM 复核"),
    ("summary", "视频总结"),
    ("polish", "观点润色"),
)
_STAGE_KEYS = {k for k, _ in STAGES}


def get_llm_profiles(session: Session, *, include_secrets: bool = False) -> list[dict]:
    """模型档案列表。默认打码 api_key（返回 key_set 供前端「留空保持」判断）。

    llm_profiles 键不存在（从未保存过）时才从旧单配置迁移；键存在（哪怕空
    列表）以键为准——用户删光档案应表示「无档案」，不能翻出旧配置。
    """
    row = session.get(AppSetting, LLM_PROFILES_KEY)
    if row is None:
        profiles = _migrate_legacy_profiles(session)
    else:
        profiles = ((row.value if row else {}) or {}).get("profiles") or []
    if include_secrets:
        return [dict(p) for p in profiles]
    return [
        {**dict(p), "api_key": "", "key_set": bool(p.get("api_key"))} for p in profiles
    ]


def _migrate_legacy_profiles(session: Session) -> list[dict]:
    """旧单配置（app_setting["llm"]，含 EPIC-05 的 review_model 补丁）→ 档案列表。

    只在读时内存转换，不写回——保存档案后以 llm_profiles 键为准。旧键保留不删，
    回滚无感。
    """
    s = get_settings()
    cfg = get_llm_settings(session)
    if not (cfg["base_url"] and cfg["api_key"]):
        return []
    row = session.get(AppSetting, LLM_SETTINGS_KEY)
    stored = (row.value if row else {}) or {}
    profiles = [
        {
            "id": "legacy",
            "name": "主模型",
            "template": cfg["template"],
            "base_url": cfg["base_url"],
            "api_key": cfg["api_key"],
            "model": cfg["model"] or s.llm_model,
            "chat_path": cfg["chat_path"] or "/chat/completions",
        }
    ]
    review_model = str(stored.get("review_model") or "").strip()
    if review_model and review_model != profiles[0]["model"]:
        profiles.append({**profiles[0], "id": "legacy-review", "name": "复核模型", "model": review_model})
    return profiles


def put_llm_profiles(session: Session, profiles_payload: list) -> list[dict]:
    """全量保存档案。api_key 留空 = 沿用该 id 现有 key；新条目 id 缺省自动生成。"""
    import uuid

    if not isinstance(profiles_payload, list):
        raise ValueError("profiles 需为数组")
    existing = {p["id"]: p for p in get_llm_profiles(session, include_secrets=True)}
    clean: list[dict] = []
    for raw in profiles_payload:
        if not isinstance(raw, dict):
            raise ValueError("profiles 条目需为对象")
        pid = str(raw.get("id") or "").strip() or f"p{uuid.uuid4().hex[:8]}"
        entry = {
            "id": pid,
            "name": str(raw.get("name") or "").strip() or str(raw.get("model") or "").strip() or "未命名模型",
            "template": str(raw.get("template") or "custom").strip(),
            "base_url": str(raw.get("base_url") or "").strip().rstrip("/"),
            "api_key": str(raw.get("api_key") or "").strip() or (existing.get(pid) or {}).get("api_key", ""),
            "model": str(raw.get("model") or "").strip(),
            "chat_path": str(raw.get("chat_path") or "").strip() or "/chat/completions",
        }
        if entry["template"] != "custom" and entry["template"] not in {t.key for t in LLM_TEMPLATES}:
            raise ValueError(f"未知模板: {entry['template']}")
        if entry["base_url"] and not entry["base_url"].startswith(("http://", "https://")):
            raise ValueError(f"{entry['name']}: base_url 需以 http(s):// 开头")
        if entry["chat_path"] and not entry["chat_path"].startswith("/"):
            raise ValueError(f"{entry['name']}: chat_path 需以 / 开头")
        clean.append(entry)
    row = session.get(AppSetting, LLM_PROFILES_KEY)
    if row is None:
        row = AppSetting(key=LLM_PROFILES_KEY, value={"profiles": clean})
        session.add(row)
    else:
        row.value = {"profiles": clean}
    session.commit()
    return get_llm_profiles(session)


def get_llm_assignment(session: Session) -> dict[str, str | None]:
    """阶段 → 档案 id；指向失效/缺省回落第一个档案。

    从旧单配置迁移而来且未手动保存过分配时，review 默认指向迁移出的
    「复核模型」档案（若存在）——保持 EPIC-05 review_model 的语义。
    """
    row = session.get(AppSetting, LLM_ASSIGNMENT_KEY)
    stored = (row.value if row else {}) or {}
    profiles = get_llm_profiles(session)
    first = profiles[0]["id"] if profiles else None
    legacy_review = next(
        (p["id"] for p in profiles if p["id"] == "legacy-review"), None
    )
    out: dict[str, str | None] = {}
    for key in _STAGE_KEYS:
        pid = stored.get(key)
        if pid and any(p["id"] == pid for p in profiles):
            out[key] = pid
        elif key == "review" and legacy_review:
            out[key] = legacy_review
        else:
            out[key] = first
    return out


def put_llm_assignment(session: Session, payload: dict) -> dict:
    """保存阶段分配。全量替换（前端整体提交）；非法 stage 或未知档案 422。"""
    profiles = get_llm_profiles(session)
    known = {p["id"] for p in profiles}
    clean: dict[str, str] = {}
    for key, value in payload.items():
        if key not in _STAGE_KEYS:
            raise ValueError(f"未知阶段: {key}")
        if value is None:
            continue
        if value not in known:
            raise ValueError(f"阶段 {key} 指向不存在的模型档案")
        clean[key] = str(value)
    row = session.get(AppSetting, LLM_ASSIGNMENT_KEY)
    if row is None:
        row = AppSetting(key=LLM_ASSIGNMENT_KEY, value=clean)
        session.add(row)
    else:
        row.value = clean
    session.commit()
    return get_llm_assignment(session)


def build_provider_for_profile(session: Session, profile: dict):
    """给指定档案临时构造 provider（连接测试用；不查 assignment）。"""
    from app.llm.provider import MockLLMProvider, OpenAICompatProvider

    s = get_settings()
    if not (profile.get("base_url") and profile.get("api_key") and profile.get("model")):
        return MockLLMProvider()
    return OpenAICompatProvider(
        base_url=profile["base_url"],
        api_key=profile["api_key"],
        model=profile["model"],
        timeout_sec=s.llm_timeout_sec,
        max_retries=s.llm_max_retries,
        chat_path=profile["chat_path"] or "/chat/completions",
        max_tokens=s.llm_max_tokens,
    )


def _resolve_profile(session: Session, task: str) -> dict | None:
    profiles = get_llm_profiles(session, include_secrets=True)
    if not profiles:
        return None
    pid = get_llm_assignment(session).get(task)
    for p in profiles:
        if p["id"] == pid:
            return p
    return None


def _legacy_provider(session: Session):
    """旧单配置链（app_setting["llm"] → env → Mock），profiles 为空时的回落。"""
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
        max_tokens=s.llm_max_tokens,
    )
