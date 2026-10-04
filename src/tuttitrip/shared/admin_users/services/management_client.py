"""Auth0 Management API: client-credentials token and user search.

Credentials come from settings only and are never logged. The token is kept in
process memory until shortly before it expires. Auth0 sits behind Cloudflare,
which blocks the default Python user agent, hence the explicit ``User-Agent``.
User search (``GET /api/v2/users``) reaches only the first 1000 matches.
"""

import asyncio
import re
import time
from enum import StrEnum
from typing import Any, cast

import httpx

from tuttitrip.shared.admin_users.schemas import (
    AdminUserRead,
    UserFilters,
    UserQuery,
)
from tuttitrip.shared.config.settings import Auth0Settings
from tuttitrip.shared.pagination.schemas import Page, SortDir

USER_AGENT = "TuttiTripBackend/1.0 (+https://tuttitrip.gburek.app)"
TIMEOUT_SECONDS = 10.0
SEARCH_WINDOW = 1000
TOKEN_MARGIN_SECONDS = 60
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


class ManagementError(Exception):
    """Auth0 could not be reached or refused the call (``reason`` is safe to log)."""

    def __init__(self, reason: str) -> None:
        """Keep the reason code.

        Args:
            reason: Short code without any secret.
        """
        super().__init__(f"Auth0 Management API failed: {reason}")
        self.reason = reason


class _Reason(StrEnum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    BAD_BODY = "bad_body"


def build_client() -> httpx.AsyncClient:
    """Create the HTTP client used for Auth0 calls.

    Returns:
        A client with the project's user agent and a timeout.
    """
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    )


def build_search(filters: UserFilters) -> str | None:
    """Translate the filters into an Auth0 (Lucene) ``q`` expression.

    Args:
        filters: The list filters.

    Returns:
        The expression, or None when nothing filters.
    """
    parts: list[str] = []
    if filters.q and (text := filters.q.strip()):
        escaped = _LUCENE_SPECIAL.sub(r"\\\1", text.lower())
        parts.append(f"(email:*{escaped}* OR name:*{escaped}*)")
    if filters.blocked is not None:
        parts.append(f"blocked:{str(filters.blocked).lower()}")
    return " AND ".join(parts) or None


def to_user(raw: dict[str, Any]) -> AdminUserRead:
    """Map one Auth0 user object.

    Args:
        raw: An element of the ``users`` array.

    Returns:
        The reduced account.
    """
    user_id = str(raw["user_id"])
    return AdminUserRead.model_validate(
        {
            "sub": user_id,
            "email": raw.get("email"),
            "name": raw.get("name"),
            "provider": user_id.split("|", 1)[0],
            "last_login": raw.get("last_login"),
            "created_at": raw.get("created_at"),
            "blocked": bool(raw.get("blocked")),
        }
    )


class ManagementClient:
    """Lists users through the Management API with a cached M2M token."""

    def __init__(
        self, auth0: Auth0Settings, http: httpx.AsyncClient | None = None
    ) -> None:
        """Keep the settings.

        Args:
            auth0: Tenant domain and Management credentials.
            http: HTTP client (tests pass one with a mock transport).
        """
        self._auth0 = auth0
        self._http = http or build_client()
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """Tell whether the M2M credentials are set.

        Returns:
            True when both client id and secret are non-empty.
        """
        secret = self._auth0.management_client_secret.get_secret_value()
        return bool(self._auth0.management_client_id and secret)

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            domain = self._auth0.domain
            payload = await self._send(
                "POST",
                f"https://{domain}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self._auth0.management_client_id,
                    "client_secret": (
                        self._auth0.management_client_secret.get_secret_value()
                    ),
                    "audience": f"https://{domain}/api/v2/",
                },
            )
            try:
                self._token = str(payload["access_token"])
                ttl = int(payload["expires_in"])
            except KeyError, TypeError, ValueError:
                raise ManagementError(_Reason.BAD_BODY) from None
            self._expires_at = time.monotonic() + max(ttl - TOKEN_MARGIN_SECONDS, 0)
            return self._token

    async def _send(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, str] | None = None,
        params: dict[str, str | int] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = await self._http.request(
                method, url, json=json, params=params, headers=headers
            )
            if response.status_code != httpx.codes.OK:
                reason = f"status_{response.status_code}"
                raise ManagementError(reason)
            body = response.json()
        except httpx.TimeoutException:
            raise ManagementError(_Reason.TIMEOUT) from None
        except httpx.HTTPError:
            raise ManagementError(_Reason.NETWORK) from None
        except ValueError:
            raise ManagementError(_Reason.BAD_BODY) from None
        if not isinstance(body, dict):
            raise ManagementError(_Reason.BAD_BODY)
        return cast("dict[str, Any]", body)

    async def list_users(self, query: UserQuery) -> Page[AdminUserRead]:
        """Search users, one page, mapped onto Auth0's paging.

        Auth0 pages are 0-based and cover only the first 1000 matches; a page
        past that window is empty with the real total.

        Args:
            query: Page, sort and filters.

        Returns:
            The page of accounts.

        Raises:
            ManagementError: Auth0 answered with an error or an unusable body.
        """
        token = await self._access_token()
        params: dict[str, str | int] = {
            "page": query.page - 1,
            "per_page": query.size,
            "include_totals": "true",
            "search_engine": "v3",
            "sort": f"{query.sort}:{-1 if query.dir is SortDir.DESC else 1}",
        }
        if search := build_search(query):
            params["q"] = search
        body = await self._send(
            "GET",
            f"https://{self._auth0.domain}/api/v2/users",
            params=params,
            token=token,
        )
        try:
            users = body["users"]
            total = int(body["total"])
            items = [to_user(u) for u in users]
        except KeyError, TypeError, ValueError:
            raise ManagementError(_Reason.BAD_BODY) from None
        return Page[AdminUserRead].of(items, total, query)
