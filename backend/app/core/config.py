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

    # --- Auth / JWT ---
    # Must be overridden with a real secret in every non-development environment.
    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # --- CORS ---
    # Comma-separated origins in the environment; parsed to a list here.
    cors_origins: str = "http://localhost:5173"

    # --- Storage (Task 3B) ---
    # Relative to the backend process's working directory. A Railway
    # persistent volume mounted at deploy time is a follow-up, not a
    # code change (see PROJECT_CONTEXT.md, Storage section).
    upload_dir: str = "storage/uploads"
    # Comma-separated, dot-prefixed. Extension-based validation only for
    # Phase 1 (python-magic content-sniffing deliberately deferred to
    # Phase 12 — see PROJECT_CONTEXT.md Section 11 #18).
    allowed_upload_extensions: str = ".pdf,.docx,.txt"
    # Enforced at the endpoint layer (here), not in storage_service —
    # deliberately deferred from Checkpoint 2 (see PROJECT_CONTEXT.md
    # Section 11 #24): the storage service persists whatever bytes
    # it's given, deciding whether a request should be allowed at all
    # belongs to the layer that first reads it.
    max_upload_size_mb: int = 20

    # --- Embeddings (Document Chunks -> Embeddings milestone) ---
    # OpenAI is the only provider; encapsulated entirely inside
    # embedding_service.py. No default for the API key — every real
    # environment must set it explicitly; tests never read this value
    # since the embedding service boundary is always mocked (see
    # tests/test_embedding_service.py and friends).
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_upload_extensions_list(self) -> list[str]:
        return [
            ext.strip().lower()
            for ext in self.allowed_upload_extensions.split(",")
            if ext.strip()
        ]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


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
