"""source_item.status 状态机（PRD §11）：只允许声明的迁移，防前端/任务乱改字符串。"""

PIPELINE_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"resolved", "failed", "ignored"},
    "resolved": {"media_ready", "failed", "ignored"},
    "media_ready": {"transcribing", "failed", "ignored"},
    "transcribing": {"transcribed", "failed"},
    "transcribed": {"extracting", "failed"},  # EPIC-04 起
    "extracting": {"reviewing", "ready", "failed"},  # ready：抽出 0 观点无东西可审
    "reviewing": {"ready", "failed"},
    "ready": set(),
    "failed": {"resolved", "ignored"},  # retry 从 resolved 重跑 prepare
    "ignored": set(),
}


class InvalidTransitionError(ValueError):
    pass


def ensure_transition(current: str, new: str) -> None:
    allowed = PIPELINE_TRANSITIONS.get(current)
    if allowed is None:
        raise InvalidTransitionError(f"未知状态: {current!r}")
    if new not in allowed:
        raise InvalidTransitionError(f"不允许 {current!r} → {new!r}（允许: {sorted(allowed)}）")
