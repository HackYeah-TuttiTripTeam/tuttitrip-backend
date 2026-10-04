"""Dependency that hands a feature the signed-in user's Google clients.

Tests replace ``get_google_http`` and ``get_token_source`` (or ``get_google``).
"""

from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends

from tuttitrip.shared.admin_users.api import get_management_client
from tuttitrip.shared.google.services.google_api import build_client
from tuttitrip.shared.google.services.google_token import (
    GoogleAccess,
    GoogleTokenSource,
)


@lru_cache(maxsize=1)
def get_google_http() -> httpx.AsyncClient:
    """Build the process-wide HTTP client for Google calls.

    Returns:
        The cached client.
    """
    return build_client()


def get_google(
    http: Annotated[httpx.AsyncClient, Depends(get_google_http)],
) -> GoogleAccess:
    """Build the Google clients over the cached Management API client.

    Args:
        http: HTTP client for Google.

    Returns:
        The access object.
    """
    return GoogleAccess(GoogleTokenSource(get_management_client()), http)


GoogleDep = Annotated[GoogleAccess, Depends(get_google)]
