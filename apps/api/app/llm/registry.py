"""Prompt 注册表（RAD-041）：按版本目录加载 prompt 包，只读、版本不可变。

修改 prompt = 新增目录（extraction@v2），绝不覆盖历史版本——历史 viewpoint 的
prompt_version 字段指向的包必须永远可复现。目录结构：
    prompts/<name>@<v>/{system.md, user_template.md, schema.json, meta.json}
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class PromptPack:
    version: str
    system: str
    user_template: str
    schema: dict


class PromptRegistry:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or PROMPTS_DIR

    def get(self, version: str) -> PromptPack:
        d = self._base / version
        if not d.is_dir():
            raise FileNotFoundError(f"prompt 版本不存在: {version}（{d}）")
        schema = json.loads((d / "schema.json").read_text())
        return PromptPack(
            version=version,
            system=(d / "system.md").read_text(),
            user_template=(d / "user_template.md").read_text(),
            schema=schema,
        )

    def versions(self) -> list[str]:
        return sorted(p.name for p in self._base.iterdir() if p.is_dir())


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    return PromptRegistry()
