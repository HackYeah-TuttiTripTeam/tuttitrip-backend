"""Typed application settings (pydantic-settings).

Every field maps to an environment variable with the ``TUTTITRIP_`` prefix.
Nested models use ``__`` as the delimiter, e.g. ``TUTTITRIP_DATABASE__HOST``.
``.env.example`` must list exactly these variables (a test enforces it).
"""

import re
from functools import lru_cache
from typing import Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "TUTTITRIP_"
ENV_NESTED_DELIMITER = "__"
MIN_RESET_SECRET_CHARS = 24


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
    # M2M application with `read:users` on the Management API; empty = the
    # admin user list answers 503. Set in host env files / CI secrets only.
    management_client_id: str = ""
    management_client_secret: SecretStr = SecretStr("")
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout of every call to Auth0 (token endpoint, Management API).",
    )


class McpSettings(BaseModel):
    """The MCP server under ``/api/v1/mcp`` (Auth0 audience = its own URL)."""

    enabled: bool = False
    # Public URL of the endpoint, without a trailing slash (RFC 8707). It is the
    # `resource` in the metadata and the Auth0 API identifier (the token
    # audience), so it must match the Auth0 API of this environment exactly.
    resource_url: str = "https://tuttitrip-api.gburek.app/api/v1/mcp"
    # Host names besides the one in `resource_url` accepted in the Host header.
    allowed_hosts: list[str] = Field(default_factory=list)

    @field_validator("resource_url")
    @classmethod
    def _url_without_trailing_slash(cls, value: str) -> str:
        if not value.startswith(("https://", "http://localhost")):
            msg = "must be an https:// URL (http only for localhost)"
            raise ValueError(msg)
        if value.endswith("/"):
            msg = "must not end with a slash (it is compared with the token audience)"
            raise ValueError(msg)
        return value


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
    plan_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        description="Start-to-close limit of plan generation and place fetching.",
    )
    llm_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        description="Start-to-close limit of pasted-plan parsing, offer evidence "
        "and justifications.",
    )
    embedding_timeout_seconds: float = Field(
        default=300.0, gt=0, description="Start-to-close limit of embedding jobs."
    )
    ping_timeout_seconds: float = Field(
        default=60.0, gt=0, description="Start-to-close limit of the smoke-test ping."
    )


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
    # Requests per minute from one IP (all outcomes count).
    rate_limit_per_minute: int = Field(default=10, ge=1)
    # Shared secret of the internal reset endpoint the worker's daily schedule
    # calls (the deploy generates it into the API and worker env files).
    # Empty = that endpoint is off (404).
    reset_secret: SecretStr = SecretStr("")

    @field_validator("token_sha256")
    @classmethod
    def _hex_digest_or_empty(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.fullmatch(r"[0-9a-f]{64}", value):
            msg = "must be a 64-character hex SHA-256 digest (or empty)"
            raise ValueError(msg)
        return value

    @field_validator("reset_secret")
    @classmethod
    def _secret_is_empty_or_long(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if secret and len(secret) < MIN_RESET_SECRET_CHARS:
            msg = f"must be empty or at least {MIN_RESET_SECRET_CHARS} characters"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _account_is_complete_when_enabled(self) -> Self:
        if self.token_sha256:
            given = {
                "USERNAME": self.username,
                "PASSWORD": self.password.get_secret_value(),
                "CLIENT_ID": self.client_id,
            }
            missing = [name for name, value in given.items() if not value]
            if missing:
                msg = f"demo is on (token_sha256 set) but {', '.join(missing)} is empty"
                raise ValueError(msg)
        return self


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
    mcp: McpSettings = Field(default_factory=McpSettings)

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
