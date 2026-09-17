from functools import lru_cache
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DEV_DATABASE_URL = "postgresql+psycopg://radar:radar@localhost:5432/radar"


class Settings(BaseSettings):
    """全局配置：仅从环境变量 / .env 注入，禁止硬编码连接串。
    生产环境必须通过环境变量/.env 注入；默认值仅限本地开发。
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    # 留空默认值用于区分“显式提供”与“未提供或留空”：非 dev 环境未提供即拒绝启动
    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "radar"
    s3_secret_key: str = "radar-secret"
    s3_bucket_media: str = "radar-media"
    llm_api_key: str = ""
    llm_base_url: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # --- EPIC-02 媒体发现 ---
    ytdlp_binary: str = "yt-dlp"
    ytdlp_timeout_sec: int = 60
    ytdlp_download_timeout_sec: int = 600  # 媒体下载远慢于 resolve，独立超时（RAD-031）
    # 抖音等平台要求新鲜访客 cookie；空=不传 --cookies（Netscape 格式 cookie 文件路径）
    ytdlp_cookies_file: str = ""
    ffmpeg_binary: str = "ffmpeg"
    ffmpeg_timeout_sec: int = 600
    media_host_allowlist: tuple[str, ...] = (
        "youtube.com",
        "youtu.be",
        "bilibili.com",
        "douyin.com",
    )
    discover_playlist_max_items: int = 50
    discover_dispatch_interval_sec: int = 300
    # 人类化错峰：同批到期账号派发时加 0~N 秒随机 countdown，避免同一秒并发打向平台；
    # 0 = 关闭（测试默认）
    discover_dispatch_stagger_max_sec: int = 0
    # --- Plan #4 抖音（外部 dtk 解析服务，部署见 infra/docker/docker-compose.douyin.yml） ---
    # 空=未配置；douyin 平台 adapter 构造即报错（三段消息，见 factory）
    douyin_api_base_url: str = ""
    douyin_api_key: str = ""
    douyin_api_timeout_sec: int = 60
    # 无水印直链落 CDN 白名单（防伪造响应把内网地址当下载源）；实测域名 douyinvod.com
    douyin_cdn_allowlist: tuple[str, ...] = ("douyin.com", "douyinvod.com")
    douyin_discover_max_pages: int = 3  # E4：单轮 discover 翻页上限（20 条/页）
    # --- Plan #4 直播值守（Task 5/6）---
    # StreamCap downloads 共享卷根目录；空 = live ingest 不启用（beat 每轮单行 no-op 日志）
    live_segments_dir: str = ""
    live_scan_interval_sec: int = 300  # beat：分片扫描
    live_close_grace_sec: int = 900  # 分片静默 ≥ 此值 → 会话视为下播收尾
    live_min_segment_sec: int = 30  # 小于视为残片跳过（F5：不计入转写偏移）
    live_max_segments_per_session: int = 120  # 防失控（4h@2min 上限量级）
    live_segment_max_attempts: int = 3  # 同分片连续失败 N 次后跳过记账（F4）
    # StreamCap recordings.json 在共享卷上的绝对路径；空 = 值守桥不启用
    recorder_config_path: str = ""
    recorder_container_name: str = "streamcap"  # 配置变更后 docker restart 的目标容器
    recorder_sync_interval_sec: int = 600  # beat：账号 → 录制器配置同步
    # --- EPIC-03 ASR（RAD-033/035） ---
    asr_provider: str = "faster_whisper"  # faster_whisper | mlx（Apple Silicon Metal）
    asr_mlx_python: str = ""  # venv_arm64 python 路径（voice-pro，见 README「ASR 引擎」）
    asr_mlx_worker: str = ""  # mlx_worker.py 路径
    asr_mlx_model: str = "mlx-community/whisper-medium"
    # medium：与 voice-pro 共用本地模型缓存（无 small），中文财经内容效果更好
    asr_model_name: str = "medium"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_beam_size: int = 5
    # 段末超 音频时长×该系数 判为幻觉丢弃（voice-pro 实战补丁，防长静音段漂移死循环）
    asr_max_segment_end_ratio: float = 1.05
    enable_whisperx: bool = False  # RAD-035：默认关，V1 不依赖
    enable_diarization: bool = False
    subtitle_lang_preference: tuple[str, ...] = ("zh-Hans", "zh", "en")  # 依次前缀匹配
    subtitle_min_chars: int = 10  # 解析后总字符数低于此视为不可用 → 走 ASR
    transcript_overlap_tolerance_ms: int = 2000  # RAD-034 重叠阈值
    prepare_max_media_duration_sec: int = 14400  # CEO-2C：超限快速失败防 CPU 长期占用
    prepare_sweep_interval_sec: int = 600  # 注记②：discovered 周期补扫（beat）
    prepare_sweep_batch_size: int = 200  # 单轮补扫派发上限，防任务风暴

    @field_validator("media_host_allowlist", mode="before")
    @classmethod
    def _parse_allowlist(cls, v: object) -> object:
        # 环境变量是 CSV（MEDIA_HOST_ALLOWLIST=youtube.com,bilibili.com），归一为小写 tuple
        if isinstance(v, str):
            return tuple(h.strip().lower() for h in v.split(",") if h.strip())
        return v

    @field_validator("douyin_cdn_allowlist", mode="before")
    @classmethod
    def _parse_douyin_cdn_allowlist(cls, v: object) -> object:
        # 同 CSV 模式（DOUYIN_CDN_ALLOWLIST=douyin.com,douyinvod.com）
        if isinstance(v, str):
            return tuple(h.strip().lower() for h in v.split(",") if h.strip())
        return v

    @field_validator("subtitle_lang_preference", mode="before")
    @classmethod
    def _parse_lang_preference(cls, v: object) -> object:
        # 同 CSV 模式（SUBTITLE_LANG_PREFERENCE=zh-Hans,zh,en）
        if isinstance(v, str):
            return tuple(p.strip() for p in v.split(",") if p.strip())
        return v

    @model_validator(mode="before")
    @classmethod
    def _resolve_database_url(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # strip：防止空格串绕过“未提供或留空”判定
        env = data.get("env", "dev")
        if isinstance(env, str):
            env = env.strip()
        url = data.get("database_url")
        if isinstance(url, str):
            url = url.strip()
        if url:
            normalized = {**data, "database_url": url}
            if "env" in normalized and isinstance(normalized["env"], str):
                normalized["env"] = env
            return normalized
        if env == "dev":
            return {**data, "database_url": LOCAL_DEV_DATABASE_URL}
        raise ValueError(
            "DATABASE_URL is required when env is not 'dev'; "
            "set the DATABASE_URL environment variable or add it to .env (see .env.example)"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
