"""Application settings (pydantic-settings, env / .env)."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "dev"
    DB_URL: str = "postgresql+asyncpg://kb:kb@localhost:5432/kb"
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "documents"
    S3_REGION: str = "us-east-1"
    S3_PUBLIC_ENDPOINT_URL: str | None = None  # for presigned URLs reachable from the browser
    DB_POOL_SIZE: int = 10
    REDIS_URL: str = "redis://localhost:6379/0"

    DB_BACKEND: Literal["postgres", "postgres_sqlalchemy", "memory"] = "postgres"
    LLM_PROVIDER: Literal["openai_compat", "stub"] = "stub"
    EMBEDDINGS_PROVIDER: Literal["openai_compat", "stub"] = "stub"
    STORAGE_BACKEND: Literal["s3", "memory"] = "s3"
    QUEUE_BACKEND: Literal["celery", "inline", "memory"] = "celery"
    AUDIT_SINKS: str = "db"  # comma-separated: db,kafka,log

    OPENAI_BASE_URL: str = "https://api.proxyapi.ru/openai/v1"
    OPENAI_API_KEY: str = Field(default="", repr=False)
    CHAT_MODEL: str = Field(default="gpt-4o-mini", validation_alias=AliasChoices("CHAT_MODEL", "LLM_MODEL"))
    EMBEDDINGS_MODEL: str = "text-embedding-3-small"
    EMBEDDINGS_DIM: int = 1536
    OPENAI_TIMEOUT_S: float = 30.0
    OPENAI_MAX_RETRIES: int = 3
    OPENAI_STRUCTURED_OUTPUTS: Literal["auto", "json_schema", "json_object"] = "auto"
    CHAT_TEMPERATURE: float = 0.0
    EMBED_BATCH_SIZE: int = 64

    INDEX_MAX_RETRIES: int = 3
    INDEX_RETRY_BACKOFF_S: int = 10
    AUDIT_PARTITIONS_MONTHS: int = 2
    RRF_K: int = 60

    MAX_UPLOAD_MB: int = 20
    AUDIT_MAX_BODY_KB: int = 10
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    DOWNLOAD_URL_TTL_S: int = 900
    UPLOAD_FORMATS: str = "txt,md,docx,pdf"  # enabled upload formats (TextExtractor adapters)

    # Stage 8: operations
    ADMIN_TOKEN: str = Field(default="", repr=False)  # empty -> /admin disabled (404)
    AUDIT_BACKEND: Literal["postgres", "clickhouse", "both"] = "postgres"
    CLICKHOUSE_HOST: str = "localhost"
    CLICKHOUSE_PORT: int = 8124  # HTTP interface (compose profile "clickhouse" maps 8124 -> 8123)
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = Field(default="", repr=False)
    CLICKHOUSE_DATABASE: str = "kb"
    CLICKHOUSE_TABLE: str = "audit_runs"
    CLICKHOUSE_SECURE: bool = False
    LLM_BASE_URL: str | None = None  # default: OPENAI_BASE_URL (Ollama: http://localhost:11434/v1)
    LLM_API_KEY: str | None = Field(default=None, repr=False)  # default: OPENAI_API_KEY
    EMBED_BASE_URL: str | None = None  # default: OPENAI_BASE_URL
    EMBED_API_KEY: str | None = Field(default=None, repr=False)  # default: OPENAI_API_KEY
    REINDEX_BATCH_SIZE: int = 64
    LOG_FORMAT: Literal["json", "text"] = "json"
    LOG_LEVEL: str = "INFO"
    METRICS_ENABLED: bool = False

    TOP_K: int = 5
    MIN_SCORE: float = 0.6  # retriever score threshold (0..1)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def llm_base_url(self) -> str:
        return self.LLM_BASE_URL or self.OPENAI_BASE_URL

    @property
    def llm_api_key(self) -> str:
        return self.LLM_API_KEY or self.OPENAI_API_KEY

    @property
    def embed_base_url(self) -> str:
        return self.EMBED_BASE_URL or self.OPENAI_BASE_URL

    @property
    def embed_api_key(self) -> str:
        return self.EMBED_API_KEY or self.OPENAI_API_KEY

    @property
    def audit_to_postgres(self) -> bool:
        return self.AUDIT_BACKEND in ("postgres", "both")

    @property
    def audit_to_clickhouse(self) -> bool:
        return self.AUDIT_BACKEND in ("clickhouse", "both")

    @property
    def upload_formats(self) -> list[str]:
        return [f.strip().lower().lstrip(".") for f in self.UPLOAD_FORMATS.split(",") if f.strip()]

    @property
    def audit_sinks(self) -> list[str]:
        return [s.strip() for s in self.AUDIT_SINKS.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
