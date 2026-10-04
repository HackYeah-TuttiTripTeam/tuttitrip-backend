"""Typed application settings (pydantic-settings).

Every field maps to an environment variable with the ``TUTTITRIP_`` prefix.
Nested models use ``__`` as the delimiter, e.g. ``TUTTITRIP_DATABASE__HOST``.
``.env.example`` must list exactly these variables (a test enforces it).
"""

import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
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
    # M2M application on the Management API; empty = the admin user list, block,
    # delete and PATCH /me/account answer 503. Scopes it needs: `read:users`
    # (list), `update:users` (block, unblock, rename) and `delete:users`
    # (delete). Host env files / CI secrets only.
    management_client_id: str = ""
    management_client_secret: SecretStr = SecretStr("")


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


class AdminSettings(BaseModel):
    """Guards of the administrator actions."""

    # Mirrors the allow-list (secret ALLOWED_DISCORD_IDS) of the Auth0 post-login
    # Action "TuttiTrip superadmins", which is the only place superadmins exist.
    protected_discord_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Discord user ids of the superadmins. An account with one of these "
            "ids cannot be blocked or deleted through the API (409)."
        ),
    )


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


class InterviewSettings(BaseModel):
    """Limits of the interview assistant (text over AG-UI, voice over WebRTC)."""

    run_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description="One text turn is cut off after this long (gateway: 300 s).",
    )
    agent_model: str | None = Field(
        default=None,
        description=(
            "Model of the interview agent; None = the catalog route tuttitrip:agent"
        ),
    )

    @field_validator("agent_model", "voice_reasoning_effort", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return value or None

    agent_thinking: bool = Field(
        default=False,
        description=(
            "Whether the OpenRouter interview agent (agent_model) may reason; "
            "off by default for the fastest answers."
        ),
    )
    trip_budget_usd: Decimal = Field(
        default=Decimal(2),
        gt=0,
        description="Spend ceiling of the text interview per trip (SpendLimits).",
    )
    qwen_usd_per_million_input_tokens: Decimal = Field(
        default=Decimal("0.30"),
        ge=0,
        description="Estimated price of Qwen on the GB10, absent from genai-prices.",
    )
    qwen_usd_per_million_output_tokens: Decimal = Field(
        default=Decimal("0.90"),
        ge=0,
        description="Estimated price of Qwen output tokens on the GB10.",
    )
    classify_min_confidence: float = Field(
        default=0.7,
        ge=0,
        le=1,
        description="Below this confidence a decision model's pick is only asked back.",
    )
    impact_budget_seconds: float = Field(
        default=3.0,
        gt=0,
        description=(
            "Time the solver may spend measuring which question changes the plan "
            "most (once per turn); past it the fixed question order is used."
        ),
    )
    card_nudges: int = Field(
        default=1,
        ge=0,
        description=(
            "How often a text turn sends the assistant back to put the next "
            "question on a card when it only typed it (0: never). Each costs a "
            "model call."
        ),
    )
    voice_model: str = Field(
        default="openai:gpt-realtime-2.1-mini",
        description="Pydantic AI realtime model of the voice interview.",
    )
    voice_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = Field(
        default=None,
        description=(
            "Reasoning effort of the realtime model (minimal, low, medium, high); "
            "None = reasoning off ('none'). Only gpt-realtime-2* models take it."
        ),
    )
    voice_max_seconds: float = Field(
        default=300.0, gt=0, description="A voice conversation is closed after this."
    )
    voice_claim_ttl_seconds: float = Field(
        default=45.0,
        gt=0,
        description=(
            "A live call holds its session this long without a heartbeat; a dead "
            "call frees the session after it."
        ),
    )
    voice_heartbeat_seconds: float = Field(
        default=10.0,
        gt=0,
        description="How often a live call extends its hold on the session.",
    )
    voice_attach_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="How long the offer waits for the server sideband to attach.",
    )
    voice_trip_seconds: int = Field(
        default=1800,
        ge=1,
        description="Voice time one trip's interview may use in all (all calls).",
    )
    voice_budget_usd: Decimal = Field(
        default=Decimal(1),
        gt=0,
        description="Cost limit of one voice conversation (UsageLimits).",
    )


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


class ExpensesSettings(BaseModel):
    """Limits of the expenses domain."""

    max_unconfirmed_receipts: int = Field(default=20, ge=1)
    """Receipt images a trip may hold before they are confirmed or expire."""


class NbpSettings(BaseModel):
    """National Bank of Poland exchange-rate API (average rates, tables A and B)."""

    base_url: str = "https://api.nbp.pl/api"
    timeout_seconds: float = Field(default=5.0, gt=0)


class PhotoSettings(BaseModel):
    """Limits of trip photos, which live in Postgres (``bytea``).

    The browser shrinks the image and makes the thumbnail before uploading
    (that also strips EXIF), so the limits are small on purpose.
    """

    # Largest accepted photo, bytes (after the browser's resize).
    max_image_bytes: int = Field(default=2_000_000, ge=1)
    # Largest accepted thumbnail, bytes; it is also inlined in the list.
    max_thumbnail_bytes: int = Field(default=60_000, ge=1)
    # Photos one trip may hold (database and backup size).
    max_per_trip: int = Field(default=200, ge=1)


class LocationSettings(BaseModel):
    """Trip location sharing: how long a shared position stays valid."""

    # A position is not returned (and is deleted) this long after its last update.
    position_ttl_minutes: int = Field(default=15, ge=1)


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


class SampleTripSettings(BaseModel):
    """The sample trip every new account gets with its first trip list.

    The trip ("Przykład: ...") is copied once per account, with a computed plan,
    ratings, an expense and a notification, so a new user sees what the app does.
    The jury accounts get it from the demo reset.
    """

    # false = nobody gets the sample trip (accounts that already have it keep it).
    enabled: bool = True
    # Catalog city of the sample trip; if it has no places yet, nobody gets the
    # trip until it has (a missing city never breaks the trip list).
    city_slug: str = "warszawa"


class PlanningSettings(BaseModel):
    """Which solver computes the plans (docs/algorytm.md, section 9)."""

    solver: Literal["local_search", "cp_sat"] = Field(
        default="local_search",
        description=(
            "The deterministic local search is the default; cp_sat swaps in the "
            "OR-Tools CP-SAT solver behind the same interface."
        ),
    )
    cpsat_max_deterministic_time: float = Field(
        default=10.0,
        gt=0,
        le=600,
        description=(
            "Deterministic seconds (not wall time) CP-SAT may use per plan, all "
            "rounds together; the local search takes over when it runs out."
        ),
    )
    cpsat_random_seed: int = Field(
        default=1, description="Fixed seed of CP-SAT, so equal data give equal plans."
    )


class CitiesSettings(BaseModel):
    """Where the demo cities' sheet comes from and lands.

    ``deploy/fetch-cities.sh`` downloads the public Google Sheet
    (``sheet_id``) into the ``tuttitrip-cities-data`` volume, mounted at
    ``data_dir``; the import command reads ``miasta.xlsx`` from there.
    """

    # Empty: the deploy skips the download and keeps the last copy.
    sheet_id: str = ""
    data_dir: Path = Path("/data/cities")


class GeocoderSettings(BaseModel):
    """The geocoder behind the city suggestions (Photon, OpenStreetMap data).

    Photon is used because Nominatim's usage policy forbids search-as-you-type.
    The public instance is a fair-use service: identify the app, cache answers
    and keep the request rate low, or run your own Photon and set ``base_url``.
    """

    base_url: str = "https://photon.komoot.io/api/"
    # Required by the usage policies: names the app, not an HTTP library.
    user_agent: str = "TuttiTrip/1.0 (+https://tuttitrip.gburek.app)"
    timeout_seconds: float = Field(default=3.0, gt=0)
    max_results: int = Field(default=10, ge=1, le=40)
    cache_ttl_seconds: int = Field(default=86400, ge=0)
    cache_max_entries: int = Field(default=1000, ge=1)
    # Per process. Over it the endpoint answers with catalog cities only.
    max_requests_per_minute: int = Field(default=60, ge=1)


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
    admin: AdminSettings = Field(default_factory=AdminSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    interview: InterviewSettings = Field(default_factory=InterviewSettings)
    dbos: DbosSettings = Field(default_factory=DbosSettings)
    jobs: JobsSettings = Field(default_factory=JobsSettings)
    nbp: NbpSettings = Field(default_factory=NbpSettings)
    expenses: ExpensesSettings = Field(default_factory=ExpensesSettings)
    demo: DemoSettings = Field(default_factory=DemoSettings)
    sample_trip: SampleTripSettings = Field(default_factory=SampleTripSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    photos: PhotoSettings = Field(default_factory=PhotoSettings)
    locations: LocationSettings = Field(default_factory=LocationSettings)
    planning: PlanningSettings = Field(default_factory=PlanningSettings)

    cities: CitiesSettings = Field(default_factory=CitiesSettings)
    geocoder: GeocoderSettings = Field(default_factory=GeocoderSettings)

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
