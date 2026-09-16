"""共享测试载荷（E4）：unit/integration 共同 import，避免跨测试模块导入。"""

URL = "https://www.youtube.com/watch?v=abc123"

# E4：此常量供 tests/unit 与 tests/integration 共同 import
SUCCESS_PAYLOAD = {
    "id": "abc123",
    "title": "美联储加息点评",
    "extractor_key": "Youtube",
    "webpage_url": URL,
    "channel_url": "https://www.youtube.com/@macro-diary",
    "thumbnail": "https://i.ytimg.com/vi/abc123/hq.jpg",
    "duration": 1250.5,
    "is_live": False,
    "upload_date": "20260315",
    "channel_id": "ch_42",
    "channel": "宏观日记",
    "subtitles": {"zh-Hans": [{"ext": "vtt"}]},
    "automatic_captions": {"en": [{"ext": "vtt"}]},
}

PLAYLIST_PAYLOAD = {
    "id": "ch_42",
    "entries": [
        {"id": "v1", "title": "视频一", "url": "https://www.youtube.com/watch?v=v1", "duration": 600},
        {"id": "v2", "title": None, "url": "https://www.youtube.com/watch?v=v2"},
        # 无 id 的坏条目应被跳过
        {"title": "broken"},
    ],
}
