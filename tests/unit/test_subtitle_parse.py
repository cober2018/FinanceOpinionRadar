"""字幕解析（RAD-031 前半）：json3/vtt → 段落；坏载荷返回空列表交编排层走 ASR。"""

from app.services.media.contracts import SubtitleResult
from app.services.media.subtitles import SubtitleSegment, is_usable, parse_subtitle

JSON3 = (
    '{"events":['
    '{"tStartMs":0,"dDurationMs":1500,"segs":[{"utf8":"今天"},{"utf8":"A股"}]},'
    '{"tStartMs":2000,"dDurationMs":1000,"segs":[{"utf8":"大涨"}]}]}'
)


def test_parse_json3() -> None:
    result = SubtitleResult(language="zh-Hans", content=JSON3.encode(), fmt="json3")
    assert parse_subtitle(result) == [
        SubtitleSegment(0, 1500, "今天A股"),
        SubtitleSegment(2000, 3000, "大涨"),
    ]


def test_parse_json3_skips_empty_and_zero_duration_events() -> None:
    raw = (
        '{"events":['
        '{"tStartMs":0,"dDurationMs":0,"segs":[{"utf8":"零时长"}]},'
        '{"tStartMs":100,"dDurationMs":500,"segs":[{"utf8":"\\n"}]},'
        '{"segs":[{"utf8":"无起点"}]},'
        '{"tStartMs":500,"dDurationMs":800,"segs":[{"utf8":"有效"}]}]}'
    )
    result = SubtitleResult(language="zh", content=raw.encode(), fmt="json3")
    assert parse_subtitle(result) == [SubtitleSegment(500, 1300, "有效")]


def test_parse_vtt() -> None:
    raw = (
        "WEBVTT\n\n"
        "NOTE 旁注块应被跳过\n\n"
        "00:00:01.000 --> 00:00:03.500\n"
        "你好 世界\n\n"
        "00:00:04.000 --> 00:00:05.000\n"
        "第二段\n"
    )
    result = SubtitleResult(language="en", content=raw.encode(), fmt="vtt")
    assert parse_subtitle(result) == [
        SubtitleSegment(1000, 3500, "你好 世界"),
        SubtitleSegment(4000, 5000, "第二段"),
    ]


def test_not_usable_when_too_short() -> None:
    short = [SubtitleSegment(0, 100, "太短")]
    assert is_usable(short, min_chars=10) is False
    long_enough = [SubtitleSegment(0, 100, "这是一段足够长的字幕内容")]
    assert is_usable(long_enough, min_chars=10) is True


def test_unparsable_returns_empty() -> None:
    bad_json = SubtitleResult(language="x", content=b"{not json", fmt="json3")
    assert parse_subtitle(bad_json) == []
    bad_vtt = SubtitleResult(language="x", content=b"\xff\xfe\x00", fmt="vtt")
    assert parse_subtitle(bad_vtt) == []


def test_unknown_fmt_treated_as_vtt() -> None:
    raw = "00:00:01.000 --> 00:00:02.000\n内容\n"
    result = SubtitleResult(language="x", content=raw.encode(), fmt="srt")
    assert parse_subtitle(result) == [SubtitleSegment(1000, 2000, "内容")]
