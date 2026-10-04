"""Fixed values used by more than one slice: HTTP header names, schemes, ids.

Values that differ per deployment do not belong here; they are fields of
``shared.config.settings.Settings``.
"""

from typing import Final

CACHE_CONTROL_HEADER: Final = "Cache-Control"
"""Response header that tells caches what they may keep."""

NO_STORE: Final = "no-store"
"""``Cache-Control`` value for responses that carry secrets or must not be cached."""

NO_STORE_HEADERS: Final = {CACHE_CONTROL_HEADER: NO_STORE}
"""Headers of an error response that must not be cached."""

RETRY_AFTER_HEADER: Final = "Retry-After"
"""Response header with the seconds a throttled client should wait."""

WWW_AUTHENTICATE_HEADER: Final = "WWW-Authenticate"
"""Response header of a 401 that names the expected authentication scheme."""

USER_AGENT_HEADER: Final = "User-Agent"
"""Request header; Cloudflare in front of Auth0 blocks the default Python value."""

AUTHORIZATION_HEADER: Final = "Authorization"
"""Request header with the credentials (``Bearer <token>``)."""

BEARER_SCHEME: Final = "Bearer"
"""RFC 6750 authentication scheme of the access tokens."""

OUTBOUND_USER_AGENT: Final = "TuttiTripBackend/1.0 (+https://tuttitrip.gburek.app)"
"""``User-Agent`` of the backend's own HTTP calls (Auth0)."""
