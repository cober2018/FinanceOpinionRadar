from app.core.settings import Settings


def test_media_defaults() -> None:
    s = Settings(env="dev", database_url="postgresql+psycopg://u:p@h/db")
    assert s.ytdlp_binary == "yt-dlp"
    assert s.ytdlp_timeout_sec == 60
    assert s.media_host_allowlist == ("youtube.com", "youtu.be", "bilibili.com", "douyin.com")
    assert s.discover_playlist_max_items == 50
    assert s.discover_dispatch_interval_sec == 300


def test_allowlist_parses_csv_and_strips() -> None:
    s = Settings(
        env="dev",
        database_url="postgresql+psycopg://u:p@h/db",
        media_host_allowlist=" YouTube.COM , bilibili.com ",
    )
    assert s.media_host_allowlist == ("youtube.com", "bilibili.com")


def test_empty_allowlist_entry_dropped() -> None:
    s = Settings(
        env="dev",
        database_url="postgresql+psycopg://u:p@h/db",
        media_host_allowlist="youtube.com,,",
    )
    assert s.media_host_allowlist == ("youtube.com",)


def test_asr_defaults() -> None:
    # voice-pro 共用模型缓存只有 base/medium/medium.en/large-v3，无 small → 默认 medium
    s = Settings(env="dev", database_url="postgresql+psycopg://u:p@h/db")
    assert s.asr_model_name == "medium"
    assert s.asr_max_segment_end_ratio == 1.05  # voice-pro 实战：段末超音频时长×1.05 判幻觉
