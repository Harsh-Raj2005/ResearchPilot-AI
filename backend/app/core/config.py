"""
Centralized application configuration.

All environment-driven settings live here, loaded via pydantic-settings.
No other module should read os.environ directly — import `settings` from
this module instead. This keeps configuration in one auditable place and
makes it trivial to override in tests.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- App ---
    app_name: str = "ResearchPilot AI"
    environment: str = "development"  # development | production
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    # Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/db
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/researchpilot"

    # --- CORS ---
    # Comma-separated origins in the environment; parsed to a list here.
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings accessor. Use this via FastAPI's dependency injection
    (see app/core/deps.py, added in a later task) rather than importing
    `settings` directly in request-handling code, so tests can override it.
    """
    return Settings()


# Module-level instance for simple, non-request-scoped use (e.g. Alembic env.py).
settings = get_settings()
