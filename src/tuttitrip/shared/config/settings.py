"""Typed application settings (pydantic-settings).

Every field maps to an environment variable with the ``TUTTITRIP_`` prefix.
Nested models use ``__`` as the delimiter, e.g. ``TUTTITRIP_DATABASE__HOST``.
``.env.example`` must list exactly these variables (a test enforces it).
"""

from functools import lru_cache

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


class LlmSettings(BaseModel):
    """Default model for Pydantic AI agents (provider keys use their own vars)."""

    model: str = "openai:gpt-5.2"


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
    # Deployed frontends (main and develop). Deploys extend it with the
    # Cloudflare Workers preview URLs of `tuttitrip-frontend`.
    cors_origin_regex: str = r"^https://tuttitrip(-develop)?\.gburek\.app$"
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    auth0: Auth0Settings = Field(default_factory=Auth0Settings)
    llm: LlmSettings = Field(default_factory=LlmSettings)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process.

    Returns:
        The cached application settings.
    """
    return Settings()
