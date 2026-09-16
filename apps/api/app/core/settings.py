from functools import lru_cache
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DEV_DATABASE_URL = "postgresql+psycopg://radar:radar@localhost:5432/radar"


class Settings(BaseSettings):
    """全局配置：仅从环境变量 / .env 注入，禁止硬编码连接串。
    生产环境必须通过环境变量/.env 注入；默认值仅限本地开发。
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    # 留空默认值用于区分“显式提供”与“未提供”：非 dev 环境未提供即拒绝启动
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

    @model_validator(mode="before")
    @classmethod
    def _resolve_database_url(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("database_url"):
            return data
        if data.get("env", "dev") == "dev":
            return {**data, "database_url": LOCAL_DEV_DATABASE_URL}
        raise ValueError(
            "DATABASE_URL is required when env is not 'dev'; "
            "set the DATABASE_URL environment variable or add it to .env (see .env.example)"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
