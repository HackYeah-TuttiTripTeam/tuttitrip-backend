"""Typed application settings (pydantic-settings).

Every field maps to an environment variable with the ``TUTTITRIP_`` prefix.
Nested models use ``__`` as the delimiter, e.g. ``TUTTITRIP_DATABASE__HOST``.
``.env.example`` must list exactly these variables (a test enforces it).
"""

from functools import lru_cache
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "TUTTITRIP_"
ENV_NESTED_DELIMITER = "__"


class DatabaseSettings(BaseModel):
    """PostgreSQL connection parameters."""

    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    user: str = "tuttitrip"
    password: SecretStr = SecretStr("tuttitrip")
    name: str = "tuttitrip"
    echo: bool = False


class Auth0Settings(BaseModel):
    """Auth0 tenant and API used to validate access tokens."""

    domain: str = "dev-yahwm2zlut2gqdry.us.auth0.com"
    audience: str = "https://tuttitrip-api.gburek.app"
    # Namespaced access-token claim with the user's roles, set by the Auth0
    # post-login Action ("admin" for the superadmin allow-list).
    roles_claim: str = "https://tuttitrip.gburek.app/roles"


class LlmSettings(BaseModel):
    """Default model for Pydantic AI agents (provider keys use their own vars)."""

    model: str = "openai:gpt-5.2"


class DbosSettings(BaseModel):
    """How the backend reaches the DBOS system database of tuttitrip-worker."""

    # Empty: derive from the database settings (same per-env database).
    system_database_url: SecretStr = SecretStr("")
    application_name: str = "tuttitrip-worker"
    # Must equal the worker's DBOS__APPVERSION (see deploy/CONVENTIONS.md).
    application_version: str = "local"


class JobsSettings(BaseModel):
    """Worker liveness thresholds used by /health and enqueue endpoints."""

    worker_stale_after_seconds: int = Field(default=90, ge=1)
    worker_missing_after_seconds: int = Field(default=600, ge=1)


class Settings(BaseSettings):
    """Root settings object for the whole application."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED_DELIMITER,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "local"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # Deployed frontends: main, develop and per-branch preview Workers
    # (tuttitrip-preview-<slug>.<account>.workers.dev). Replace the second
    # [a-z0-9-]+ with the account's workers.dev subdomain once it is known.
    cors_origin_regex: str = (
        r"^https://(tuttitrip(-develop)?\.gburek\.app"
        r"|tuttitrip-preview-[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev)$"
    )
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth0: Auth0Settings = Field(default_factory=Auth0Settings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    dbos: DbosSettings = Field(default_factory=DbosSettings)
    jobs: JobsSettings = Field(default_factory=JobsSettings)

    def dbos_system_database_url(self) -> str:
        """DBOS system database URL, defaulting to the app database.

        Returns:
            A ``postgresql://`` URL (DBOS always connects with psycopg 3).
        """
        explicit = self.dbos.system_database_url.get_secret_value()
        if explicit:
            return explicit
        db = self.database
        password = quote(db.password.get_secret_value(), safe="")
        user = quote(db.user, safe="")
        return f"postgresql://{user}:{password}@{db.host}:{db.port}/{db.name}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process.

    Returns:
        The cached application settings.
    """
    return Settings()
