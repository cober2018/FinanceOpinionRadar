"""danmaku 解析器单测（Plan #5 Task 3）：类型化抽取/uint64 字符串兼容/确定性合成 id/chaos。

fixture 来源：sample_system_offline.json 为 douyinLive v2.2.1 真栈实录（Task 1 决策记录）；
sample_*_message.json 按 new_douyin.proto + protojson 序列化规则合成（真实弹幕样本
挂 Task 5 首个开播窗口冒烟校准）。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.danmaku.parse import (
    classify,
    is_room_not_found,
    loads_line,
    parse_business,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "danmaku"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --- 分流 ---


def test_classify_system_and_business():
    assert classify(_load("sample_system_offline.json")) == "system"
    assert classify(_load("sample_chat_message.json")) == "business"
    assert classify({"foo": 1}) == "unknown"
    assert classify({"method": "SomethingElse"}) == "unknown"


def test_is_room_not_found_only_for_exact_code():
    assert is_room_not_found({"event": "live_status", "code": "ROOM_NOT_FOUND"}) is True
    assert is_room_not_found({"event": "live_status", "code": "ROOM_OFFLINE"}) is False
    assert is_room_not_found({"msg": "x"}) is False


def test_loads_line_rejects_bad_json():
    assert loads_line('{"a":1}') == {"a": 1}
    assert loads_line("not json") is None
    assert loads_line("[1,2]") is None  # 非 object


# --- chat ---


def test_parse_chat_message_full_fields():
    record = parse_business(_load("sample_chat_message.json"))
    assert record is not None
    assert record.msg_type == "WebcastChatMessage"
    assert record.external_msg_id == "7299483746218392832"  # protojson uint64 → 字符串
    assert record.user_id == "4455667788"
    assert record.user_name == "小散一枚"
    assert record.text == "主播怎么看明天的大盘？"
    assert record.published_at == datetime.fromtimestamp(1726660425, tz=UTC)


def test_parse_chat_missing_msg_id_synthesizes_deterministically():
    doc = _load("sample_chat_no_msgid.json")
    first = parse_business(doc)
    second = parse_business(json.loads(json.dumps(doc)))  # 重放路径
    assert first is not None and second is not None
    assert first.external_msg_id == second.external_msg_id
    assert first.external_msg_id.startswith("s")
    assert len(first.external_msg_id) <= 64
    # 内容不同 → 合成 id 不同
    other = dict(doc, content="另一条内容")
    other_record = parse_business(other)
    assert other_record is not None
    assert other_record.external_msg_id != first.external_msg_id


# --- gift / like / member ---


def test_parse_gift_message():
    record = parse_business(_load("sample_gift_message.json"))
    assert record is not None
    assert record.msg_type == "WebcastGiftMessage"
    assert record.gift_name == "粉丝团灯牌"
    assert record.repeat_count == 1  # "1" → 1
    assert record.text is None


def test_parse_like_and_member():
    like = parse_business(_load("sample_like_message.json"))
    assert like is not None and like.like_count == 2
    member = parse_business(_load("sample_member_message.json"))
    assert member is not None and member.member_count == 1234


def test_parse_unsupported_method_returns_none():
    assert parse_business({"method": "WebcastRoomUserSeqMessage", "common": {}}) is None


# --- 时间与脏数据 chaos（F3） ---


def test_create_time_sec_form_and_garbage():
    sec = parse_business({"method": "WebcastChatMessage", "common": {"createTime": 1726660425}})
    assert sec is not None and sec.published_at == datetime.fromtimestamp(1726660425, tz=UTC)
    garbage = parse_business({"method": "WebcastChatMessage", "common": {"createTime": "soon"}})
    assert garbage is not None and garbage.published_at is None


@pytest.mark.parametrize(
    "doc",
    [
        {},
        {"method": "WebcastChatMessage"},  # 无 common/user
        {"method": "WebcastChatMessage", "common": None, "user": None, "content": None},
        {"method": 42, "common": {"msgId": 123}},
    ],
)
def test_chaos_dirty_docs_never_raise(doc):
    try:
        record = parse_business(doc)
    except Exception as exc:  # noqa: BLE001  chaos 契约：不允许抛
        pytest.fail(f"parse_business raised {exc!r}")
    if record is not None:
        assert record.msg_type in (
            "WebcastChatMessage",
            "WebcastGiftMessage",
            "WebcastLikeMessage",
            "WebcastMemberMessage",
            "WebcastSocialMessage",
        )


# --- 真栈实录校准（2026-09-19 西楚老温房间） ---


def test_envelope_line_received_at_fallback():
    """web 画像 protojson 省略零值 createTime → published_at 用采集接收时间兜底。"""
    from app.services.danmaku.ingest import parse_envelope_line

    envelope = {
        "v": 1,
        "received_at": "2026-09-18T16:39:00+00:00",
        "msg": {
            "method": "WebcastChatMessage",
            "common": {"msgId": "7686915575604024363"},  # 无 createTime（实录形态）
            "user": {"id": "1111111111111111111", "nickname": "心***"},
            "content": "消费",
        },
    }
    record = parse_envelope_line(json.dumps(envelope))
    assert record is not None
    assert record.published_at == datetime.fromisoformat("2026-09-18T16:39:00+00:00")


def test_user_name_falls_back_to_desensitized_nickname():
    record = parse_business({
        "method": "WebcastChatMessage",
        "common": {"msgId": "1"},
        "user": {"id": "1111", "desensitizedNickname": "心***"},
        "content": "光模块",
    })
    assert record is not None
    assert record.user_name == "心***"
