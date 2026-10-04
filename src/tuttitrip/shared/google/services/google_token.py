"""The signed-in user's Google access token, read through Auth0.

Auth0 stores the token Google issued at the last Google login (with the scopes
the app asked for in ``connection_scope``) and hands it to the Management API
(``read:user_idp_tokens``). The token is returned to the caller only; it is
never logged and never stored by this service.
"""

from dataclasses import dataclass

import httpx

from tuttitrip.shared.admin_users.services.management_client import (
    ManagementClient,
    ManagementError,
)
from tuttitrip.shared.google.constants import GOOGLE_IDENTITY_PROVIDER
from tuttitrip.shared.google.schemas import GoogleAccessError, GoogleErrorCode
from tuttitrip.shared.google.services.google_api import CalendarApi, DriveApi

NOT_CONNECTED = "The account has no Google sign-in with an access token"
UNAVAILABLE = "Auth0 could not provide the Google token"


class GoogleTokenSource:
    """Looks up the Google access token of an Auth0 user."""

    def __init__(self, management: ManagementClient) -> None:
        """Keep the Management API client.

        Args:
            management: Client of the Auth0 Management API.
        """
        self._management = management

    async def access_token(self, sub: str) -> str:
        """The user's current Google access token.

        Args:
            sub: Auth0 user id.

        Returns:
            The token (valid for about an hour after the Google login).

        Raises:
            GoogleAccessError: ``not_connected`` when the account did not sign
                in with Google or Auth0 holds no token, ``unavailable`` when
                the Management API is not configured or failed.
        """
        if not self._management.configured:
            raise GoogleAccessError(GoogleErrorCode.UNAVAILABLE, UNAVAILABLE)
        try:
            token = await self._management.identity_access_token(
                sub, GOOGLE_IDENTITY_PROVIDER
            )
        except ManagementError as exc:
            raise GoogleAccessError(GoogleErrorCode.UNAVAILABLE, UNAVAILABLE) from exc
        if token is None:
            raise GoogleAccessError(GoogleErrorCode.NOT_CONNECTED, NOT_CONNECTED)
        return token


@dataclass(frozen=True, slots=True)
class GoogleAccess:
    """Google clients for one user: their token source plus the HTTP client."""

    tokens: GoogleTokenSource
    http: httpx.AsyncClient

    async def calendar(self, sub: str) -> CalendarApi:
        """Calendar client with the user's token.

        Args:
            sub: Auth0 user id.

        Returns:
            The client.
        """
        return CalendarApi(self.http, await self.tokens.access_token(sub))

    async def drive(self, sub: str) -> DriveApi:
        """Drive client with the user's token.

        Args:
            sub: Auth0 user id.

        Returns:
            The client.
        """
        return DriveApi(self.http, await self.tokens.access_token(sub))
