from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://kvitto:kvitto@localhost:5432/kvitto"
    redis_url: str = "redis://localhost:6379/0"
    redis_prefix: str = "kvitto"
    request_timeout_seconds: int = Field(default=15, ge=1, le=120)
    reply_ttl_seconds: int = Field(default=60, ge=1)
    reclaim_idle_ms: int = Field(default=5000, ge=100)
    heartbeat_ttl_seconds: int = Field(default=10, ge=3)
    webhook_secret: str = ""
