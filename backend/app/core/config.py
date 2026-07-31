"""Application settings.

All configuration is sourced from environment variables. No secrets are
hardcoded. The ``MODEL_API_KEY`` may be empty for local tests and no-key
development; in that case the model gateway selects the fake provider.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "job-search-agent"
    app_env: Literal["local", "test", "prod"] = "local"
    api_v1_prefix: str = "/api/v1"

    # --- Database (PostgreSQL) ---
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/job_search_agent"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Model gateway ---
    # Provider is resolved at gateway construction time: when the API key is
    # empty or the provider is explicitly ``fake``, tests run without network.
    model_provider: Literal["deepseek", "openai", "fake", "auto"] = "auto"
    model_base_url: str = "https://api.deepseek.com"
    model_api_key: str = ""
    model_default_model: str = "deepseek-chat"

    @property
    def effective_provider(self) -> Literal["deepseek", "openai", "fake"]:
        if self.model_provider != "auto":
            return self.model_provider
        if not self.model_api_key:
            return "fake"
        return "deepseek"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
