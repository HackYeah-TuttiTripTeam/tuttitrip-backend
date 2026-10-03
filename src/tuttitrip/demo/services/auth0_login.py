"""Log the demo account in with the Auth0 ``password-realm`` grant.

The username, password and client credentials come from the settings only and
are never logged. Auth0 sits behind Cloudflare, which blocks the default
Python user agent, hence the explicit ``User-Agent``.
"""

import httpx

from tuttitrip.demo.schemas import DemoSession
from tuttitrip.shared.config.settings import Auth0Settings, DemoSettings

PASSWORD_REALM = "http://auth0.com/oauth/grant-type/password-realm"  # ruff: ignore[hardcoded-password-string]  # grant type URI
USER_AGENT = "TuttiTripBackend/1.0 (+https://tuttitrip.gburek.app)"
TIMEOUT_SECONDS = 10.0
TIMEOUT, NETWORK, BAD_BODY = "timeout", "network", "bad_body"


class DemoLoginError(Exception):
    """Auth0 refused or could not be reached.

    ``reason`` is a short code safe to log (``status_403``, ``timeout``,
    ``network``, ``bad_body``); the response text is never kept.
    """

    def __init__(self, reason: str) -> None:
        """Keep the reason code.

        Args:
            reason: Short code without any secret.
        """
        super().__init__(f"Auth0 demo login failed: {reason}")
        self.reason = reason


def build_client() -> httpx.AsyncClient:
    """Create the HTTP client used for the Auth0 call.

    Returns:
        A client with the project's user agent and a timeout.
    """
    return httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    )


async def login(
    client: httpx.AsyncClient, auth0: Auth0Settings, demo: DemoSettings
) -> DemoSession:
    """Exchange the demo account's credentials for Auth0 tokens.

    Args:
        client: HTTP client (see ``build_client``).
        auth0: Tenant domain and API audience.
        demo: Demo account credentials and Auth0 application.

    Returns:
        The access token (and a refresh token when allowed).

    Raises:
        DemoLoginError: Auth0 answered with an error or an unusable body.
    """
    body = {
        "grant_type": PASSWORD_REALM,
        "realm": demo.realm,
        "username": demo.username,
        "password": demo.password.get_secret_value(),
        "client_id": demo.client_id,
        "audience": auth0.audience,
        "scope": "openid offline_access" if demo.offline_access else "openid",
    }
    secret = demo.client_secret.get_secret_value()
    if secret:
        body["client_secret"] = secret
    try:
        response = await client.post(f"https://{auth0.domain}/oauth/token", json=body)
        if response.status_code != httpx.codes.OK:
            reason = f"status_{response.status_code}"
            raise DemoLoginError(reason)
        payload = response.json()
        return DemoSession(
            access_token=payload["access_token"],
            expires_in=int(payload["expires_in"]),
            refresh_token=payload.get("refresh_token") if demo.offline_access else None,
        )
    except httpx.TimeoutException:
        raise DemoLoginError(TIMEOUT) from None
    except httpx.HTTPError:
        raise DemoLoginError(NETWORK) from None
    except ValueError, KeyError, TypeError:
        raise DemoLoginError(BAD_BODY) from None
