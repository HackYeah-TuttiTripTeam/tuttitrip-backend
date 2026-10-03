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
    """Model providers behind the Pydantic AI model catalog.

    The GB10 host serves the Qwen models and the decision models (basal, Laya)
    behind one key; OpenRouter is the cloud fallback and serves JEV.
    """

    # GB10 (OpenAI-compatible Qwen endpoint, LiteLLM key).
    gb10_base_url: str = "https://llm.gburek.app/v1"
    gb10_api_key: SecretStr = SecretStr("")
    gb10_agent_model: str = "qwen3.8-27b"
    gb10_chat_model: str = "qwen3.8-27b-chat"
    # Decision models (System One API on the same host and key).
    basal_base_url: str = "https://llm.gburek.app/basal/v1"
    basal_model: str = "basal"
    laya_base_url: str = "https://llm.gburek.app/laya/v1"
    laya_model: str = "laya"
    # OpenRouter. Empty key = fall back to the standard OPENROUTER_API_KEY.
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3.8-flash"
    jev_model: str = "typesafe/jev-1.13"


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


class DemoSettings(BaseModel):
    """One-link jury login onto a regular demo account (``POST /auth/demo``).

    Empty ``token_sha256`` switches the route off (404). The account's
    username and password live only here, in the host's environment.
    """

    # Hex SHA-256 of the demo token from the link (`#t=<token>`).
    token_sha256: str = ""
    username: str = ""
    password: SecretStr = SecretStr("")
    # Auth0 application allowed to use the password-realm grant.
    client_id: str = ""
    # Only if that application is confidential (a regular web app).
    client_secret: SecretStr = SecretStr("")
    realm: str = "Username-Password-Authentication"
    # Ask for `offline_access` so the response carries a refresh token (the
    # Auth0 application must allow refresh tokens).
    offline_access: bool = False
    # Auth0 `sub` of the demo account. Empty: learned from a demo login.
    user_sub: str = ""
    # Requests per minute from one IP (all outcomes count).
    rate_limit_per_minute: int = Field(default=10, ge=1)


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
    demo: DemoSettings = Field(default_factory=DemoSettings)

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
