"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "duckdb:///./data/demo.duckdb"

    # LLM
    llm_provider: str = "anthropic"  # anthropic | openai | stub
    llm_model: str = "claude-sonnet-5"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Guardrails
    guardrail_default_row_limit: int = 1000
    guardrail_max_subquery_depth: int = 3
    guardrail_max_estimated_rows: int = 1_000_000
    guardrail_statement_timeout_ms: int = 5000

    # App
    audit_log_path: str = "audit.log"
    feedback_log_path: str = "feedback.jsonl"

    # Auth
    auth_db_url: str = "sqlite:///./data/auth.db"
    auth_secret_key: str = ""  # empty -> random per-process secret (see app/auth.py)
    auth_token_ttl_min: int = 720  # 12 hours
    auth_seed_demo: bool = True
    auth_demo_user: str = "demo"
    auth_demo_password: str = "demo12345"

    # CORS — comma-separated origins allowed to call the API cross-origin.
    # Empty (default) = same-origin only (the /login page is served by the API).
    # Set this to host the login page / a frontend on a different origin.
    cors_allow_origins: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def effective_provider(self) -> str:
        """Fall back to the offline stub when the chosen provider has no key."""
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            return "stub"
        if self.llm_provider == "openai" and not self.openai_api_key:
            return "stub"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
