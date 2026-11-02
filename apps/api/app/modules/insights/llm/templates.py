"""国产大模型服务商标记模板（EPIC-04+）：内置预设，支持自定义覆盖。

全部走 OpenAI 兼容协议（chat/completions + Bearer key）；个别服务的路径差异
（如 MiniMax 的 chatcompletion_v2）用 chat_path 表达。模板只是「预填」，所有字段
在设置页均可改——中转/聚合网关选 custom 手填即可。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMTemplate:
    key: str
    label: str
    base_url: str
    models: tuple[str, ...]
    chat_path: str = "/chat/completions"
    note: str = ""


LLM_TEMPLATES: tuple[LLMTemplate, ...] = (
    LLMTemplate(
        key="deepseek",
        label="DeepSeek（深度求索）",
        base_url="https://api.deepseek.com/v1",
        models=("deepseek-chat", "deepseek-reasoner"),
        note="deepseek-chat 通用；deepseek-reasoner 推理模型，价格更高",
    ),
    LLMTemplate(
        key="glm",
        label="GLM（智谱）",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4-flash", "glm-4-air", "glm-4-plus"),
        note="glm-4-flash 免费额度大，适合观点抽取的批量场景",
    ),
    LLMTemplate(
        key="minimax",
        label="MiniMax",
        base_url="https://api.minimax.chat/v1",
        models=("MiniMax-Text-01", "abab6.5s-chat"),
        chat_path="/text/chatcompletion_v2",
        note="MiniMax 兼容路径为 /text/chatcompletion_v2（模板已带）",
    ),
    LLMTemplate(
        key="kimi",
        label="Kimi（月之暗面）",
        base_url="https://api.moonshot.cn/v1",
        models=("moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0711-preview"),
    ),
    LLMTemplate(
        key="qwen",
        label="通义千问（阿里百炼）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen-plus", "qwen-turbo", "qwen-max"),
    ),
    LLMTemplate(
        key="custom",
        label="自定义 / 中转网关",
        base_url="",
        models=(),
        note="填中转地址（OpenAI 兼容），如 https://your-gateway.com/v1",
    ),
)


def get_template(key: str) -> LLMTemplate | None:
    for t in LLM_TEMPLATES:
        if t.key == key:
            return t
    return None
