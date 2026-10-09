from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """OpsBrain Application Configuration Settings."""

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # API Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # PostgreSQL Database
    POSTGRES_USER: str = "opsbrain_user"
    POSTGRES_PASSWORD: str = "opsbrain_password"
    POSTGRES_DB: str = "opsbrain"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = "postgresql+asyncpg://opsbrain_user:opsbrain_password@localhost:5432/opsbrain"

    # Ollama Local Configuration (Milestone 2+)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_EMBED_MODEL: str = "nomic-embed-text"
    OLLAMA_LLM_MODEL: str = "qwen2.5:7b"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
