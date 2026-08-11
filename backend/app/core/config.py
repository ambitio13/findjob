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

    # --- Authentication (Phase 0 security guardrail) ---
    # ``auth_secret_key`` signs access tokens. It MUST be set in prod; when
    # empty (local/test) token issuance is disabled and requests fall back to
    # the legacy ``X-User-Id`` / demo-user resolution.
    auth_secret_key: str = ""
    auth_token_ttl_minutes: int = 720  # 12h
    # Optional invite code gating registration (single-tenant deployments).
    # Empty string disables the gate.
    auth_invite_code: str = ""

    # --- Current user (legacy dev fallback; no auth) ---
    # When no ``X-User-Id`` header is present AND no bearer token is provided,
    # requests in non-prod environments are attributed to this fixed
    # development user. In prod this fallback is never used.
    demo_user_id: str = "demo_user"

    # --- Rate limiting (Phase 0 security guardrail) ---
    # Redis-backed fixed-window counters. The limiter fails open when Redis is
    # unavailable so an outage cannot take down reads.
    rate_limit_enabled: bool = True
    rate_limit_global_per_minute: int = 300
    rate_limit_model_per_minute: int = 30
    rate_limit_auth_per_minute: int = 10

    # --- Resume upload (MVP local storage) ---
    # Directory for persisted resume files. In docker this is a named volume
    # mount; local dev overrides via env (e.g. ``./data/resumes``).
    resume_upload_dir: str = "/data/resumes"
    resume_max_size_mb: int = 10

    # --- Database (PostgreSQL) ---
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/job_search_agent"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Queue (arq worker runtime) ---
    # Redis is reused as the queue transport. The namespace prefixes arq queue
    # and job keys so multiple environments can share one Redis instance.
    queue_namespace: str = "job-search-agent"
    queue_job_timeout: int = 300  # seconds; hard cap per job execution
    queue_max_retries: int = 2  # arq retry attempts on uncaught handler errors

    # --- Model gateway ---
    # Provider is resolved at gateway construction time: when the API key is
    # empty or the provider is explicitly ``fake``, tests run without network.
    model_provider: Literal["deepseek", "openai", "fake", "auto"] = "auto"
    model_base_url: str = "https://api.deepseek.com"
    model_api_key: str = ""
    model_default_model: str = "deepseek-chat"

    # --- BOSS platform adapter (env-gated pilot) ---
    # The real Playwright-backed BOSS adapter is enabled only when this flag is
    # truthy. When unset (default), the fake adapter is used for deterministic
    # tests and local dev. ``boss_session_profile_dir`` points to a local
    # Playwright persistent-context profile directory containing the user's
    # existing BOSS Web session. It is **process config** — never a request
    # payload, queue payload, or database value — so session references never
    # reach the durable layer (design.md §Phase 0).
    boss_adapter_enabled: bool = False
    boss_session_profile_dir: str = ""
    # CDP endpoint for connecting to a real, already-logged-in Chrome instance
    # (e.g. ``http://127.0.0.1:9222``). When set, the runtime uses
    # ``connect_over_cdp`` instead of ``launch_persistent_context``. This avoids
    # BOSS anti-automation detection that blocks Playwright-launched browsers.
    # Like ``boss_session_profile_dir``, this is **process config** — never a
    # request payload, queue payload, or database value.
    boss_cdp_endpoint: str = ""
    # When truthy, the userscript bridge adapter is used instead of the
    # Playwright/CDP adapter. The userscript runs in the page's own JS context
    # (via Tampermonkey), sidestepping BOSS CDP-level automation detection.
    # Like the other BOSS config values, this is **process config** — never a
    # request payload, queue payload, or database value.
    boss_userscript_bridge_enabled: bool = False
    # Shared-secret channel token for the userscript bridge endpoints. When
    # set, every bridge request must carry a matching ``X-Bridge-Token``
    # header. When empty the bridge stays open in local/test (legacy
    # behaviour) but is refused in prod. Process config only — never a
    # request payload, queue payload, or database value.
    boss_bridge_channel_token: str = ""

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
