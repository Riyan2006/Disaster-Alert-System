"""
Setu backend — configuration.

Central place for all settings and secrets, loaded from environment
variables (see .env.example for the full list). Nothing in this file
should ever contain a real key — real keys live only in a local .env
file (gitignored) or in Render's environment variable dashboard once
deployed.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    # Database (Supabase Postgres connection string) — set in Phase A step 3
    database_url: str = ""

    # Live data source API keys — set in Phase B
    openweathermap_api_key: str = ""
    aqicn_api_token: str = ""

    # LLM provider key — set in Phase D
    anthropic_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    """Cached so we don't re-read environment variables on every call."""
    return Settings()
