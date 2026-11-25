from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """OpsBrain Application Configuration Settings."""

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # API Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # PostgreSQL runtime and migration connections
    DATABASE_URL: str = (
        "postgresql+asyncpg://opsbrain_app:"
        "opsbrain_app_password@localhost:5432/opsbrain"
    )
    MIGRATION_DATABASE_URL: str = (
        "postgresql+asyncpg://opsbrain_owner:"
        "opsbrain_owner_password@localhost:5432/opsbrain"
    )
    WORKER_DATABASE_URL: str = (
        "postgresql+asyncpg://opsbrain_worker:"
        "opsbrain_worker_password@localhost:5432/opsbrain"
    )

    # Ollama Local Configuration (Milestone 2+)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"
    OLLAMA_LLM_MODEL: str = "qwen2.5:7b"

    # Development-only opaque token -> principal UUID mapping for the minimal
    # query endpoint. Keep this in local environment configuration, never Git.
    LOCAL_QUERY_TOKEN_MAP_JSON: str = "{}"
    QUERY_MIN_VECTOR_SCORE: float = 0.25

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
