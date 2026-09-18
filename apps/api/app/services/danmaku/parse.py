"""douyinLive WS 消息解析（Plan #5）。

输入为 douyinLive 转发的一行 JSON：系统消息（type=system，控制流）与业务消息
（protobuf protojson camelCase + 顶层注入 method/livename/title/avatarThumb，
见 jwwsjlm/douyinLive room_session.go buildEventJSON）。
protojson 的 int64/uint64 一律是字符串形态（"123"），取值做 string|int 双兼容；
缺失/脏字段一律 None，不抛异常（chaos 安全，F3）。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

# 入库的业务消息类型；其余类型仅存 jsonl 原始档案（F3），不解析入库
TYPED_METHODS = frozenset({
    "WebcastChatMessage",
    "WebcastGiftMessage",
    "WebcastLikeMessage",
    "WebcastMemberMessage",
    "WebcastSocialMessage",
})


@dataclass(slots=True)
class DanmakuRecord:
    """单条弹幕事件的类型化抽取结果（对应 live_chat_message 行）。"""

    msg_type: str
    external_msg_id: str
    user_id: str | None = None
    user_name: str | None = None
    text: str | None = None
    gift_name: str | None = None
    repeat_count: int | None = None
    like_count: int | None = None
    member_count: int | None = None
    published_at: datetime | None = None


def loads_line(text: str) -> dict | None:
    """单行 WS 文本 → dict；坏 JSON / 非 object 返回 None（调用方仍需归档原文）。"""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) else None


def classify(doc: dict) -> str:
    """消息类别：system（服务端状态通知）/ business（直播业务消息）/ unknown。"""
    if doc.get("type") == "system":
        return "system"
    method = doc.get("method")
    if isinstance(method, str) and method.startswith("Webcast"):
        return "business"
    return "unknown"


def is_room_not_found(doc: dict) -> bool:
    """ROOM_NOT_FOUND：房间号无效，服务端已断开且不会重连，采集应立即退出（F6）。"""
    return doc.get("event") == "live_status" and doc.get("code") == "ROOM_NOT_FOUND"


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):  # bool 是 int 子类，显式排除
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():  # 计数无负值语义
        return int(value.strip())
    return None


def _as_str(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _parse_create_time(value: object) -> datetime | None:
    """common.createTime：毫秒 epoch 为主；>1e12 按 ms、>1e9 按秒归一，其余 None。"""
    ts = _as_int(value)
    if ts is None:
        return None
    if ts > 1_000_000_000_000:
        return datetime.fromtimestamp(ts / 1000, tz=UTC)
    if ts > 1_000_000_000:
        return datetime.fromtimestamp(ts, tz=UTC)
    return None


def _sub_dict(doc: dict, key: str) -> dict:
    value = doc.get(key)
    return value if isinstance(value, dict) else {}


def _synthetic_msg_id(doc: dict) -> str:
    """msgId 缺失时的确定性合成 id（F5）：同消息重放解析恒同 id，重放可去重。"""
    common = _sub_dict(doc, "common")
    user = _sub_dict(doc, "user")
    basis = "|".join([
        _as_str(doc.get("method")) or "",
        _as_str(common.get("createTime")) or "",
        _as_str(user.get("id")) or "",
        _as_str(doc.get("content")) or "",
    ])
    return "s" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:31]


def parse_business(doc: dict) -> DanmakuRecord | None:
    """业务消息 → 类型化记录；不支持/字段脏 → None（原文仍留 jsonl 档案，F3）。"""
    method = doc.get("method")
    if not isinstance(method, str) or method not in TYPED_METHODS:
        return None
    common = _sub_dict(doc, "common")
    user = _sub_dict(doc, "user")
    msg_id = _as_str(common.get("msgId"))
    record = DanmakuRecord(
        msg_type=method,
        external_msg_id=msg_id or _synthetic_msg_id(doc),
        user_id=_as_str(user.get("id")),
        user_name=_as_str(user.get("nickname"))
        or _as_str(user.get("desensitizedNickname")),  # 匿名 web 会话昵称脱敏（真栈实录）
        published_at=_parse_create_time(common.get("createTime")),
    )
    if method == "WebcastChatMessage":
        record.text = _as_str(doc.get("content"))
    elif method == "WebcastGiftMessage":
        record.gift_name = _as_str(_sub_dict(doc, "gift").get("name"))
        repeat = _as_int(doc.get("repeatCount"))
        record.repeat_count = repeat if repeat is not None else _as_int(doc.get("count"))
    elif method == "WebcastLikeMessage":
        record.like_count = _as_int(doc.get("count"))
    elif method == "WebcastMemberMessage":
        record.member_count = _as_int(doc.get("memberCount"))
    return record
