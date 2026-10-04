"""Google token lookup through Auth0 and the Calendar and Drive clients (mocked)."""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from functools import wraps

import httpx
import pytest

from tuttitrip.shared.admin_users.services.management_client import ManagementClient
from tuttitrip.shared.config.settings import Auth0Settings
from tuttitrip.shared.google.schemas import GoogleAccessError, GoogleErrorCode
from tuttitrip.shared.google.services.google_api import (
    CalendarApi,
    DriveApi,
    GoogleNotFoundError,
)
from tuttitrip.shared.google.services.google_token import GoogleTokenSource


def run[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    """Run an async test (the suite has no async plugin)."""

    @wraps(test)
    def sync(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return sync


GOOGLE_TOKEN = "ya29.google-secret-token"
M2M_SECRET = "m2m-secret-for-tests-only"


def management(
    identities: list[dict[str, object]], *, configured: bool = True
) -> tuple[ManagementClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200, json={"access_token": "mgmt", "expires_in": 3600}
            )
        seen.append(request)
        return httpx.Response(200, json={"identities": identities})

    auth0 = Auth0Settings.model_validate(
        {"management_client_id": "cid", "management_client_secret": M2M_SECRET}
        if configured
        else {}
    )
    client = ManagementClient(
        auth0, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return client, seen


GOOGLE: dict[str, object] = {"provider": "google-oauth2", "access_token": GOOGLE_TOKEN}


@run
async def test_the_google_token_is_read_from_the_identities() -> None:
    client, seen = management([{"provider": "auth0"}, GOOGLE])
    assert (
        await GoogleTokenSource(client).access_token("google-oauth2|1") == GOOGLE_TOKEN
    )
    assert seen[0].url.path == "/api/v2/users/google-oauth2|1"
    assert seen[0].url.params["fields"] == "identities"
    assert seen[0].headers["authorization"] == "Bearer mgmt"


@pytest.mark.parametrize(
    "identities",
    [[], [{"provider": "auth0"}], [{"provider": "google-oauth2"}]],
    ids=["none", "database-user", "google-without-token"],
)
@run
async def test_no_google_token_is_not_connected(
    identities: list[dict[str, object]],
) -> None:
    client, _ = management(identities)
    with pytest.raises(GoogleAccessError) as caught:
        await GoogleTokenSource(client).access_token("auth0|1")
    assert caught.value.code is GoogleErrorCode.NOT_CONNECTED


@run
async def test_an_unconfigured_management_api_is_unavailable() -> None:
    client, seen = management([GOOGLE], configured=False)
    with pytest.raises(GoogleAccessError) as caught:
        await GoogleTokenSource(client).access_token("google-oauth2|1")
    assert caught.value.code is GoogleErrorCode.UNAVAILABLE
    assert seen == []


def google_http(
    handler: httpx.MockTransport | None = None,
    *,
    status: int = 200,
    body: object = None,
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.AsyncClient(transport=handler or httpx.MockTransport(answer)), seen


@run
async def test_a_calendar_is_created_with_the_bearer_token() -> None:
    http, seen = google_http(body={"id": "cal-1"})
    calendar_id = await CalendarApi(http, GOOGLE_TOKEN).create_calendar(
        "TuttiTrip: Gdańsk", "Europe/Warsaw"
    )
    assert calendar_id == "cal-1"
    assert seen[0].headers["authorization"] == f"Bearer {GOOGLE_TOKEN}"
    assert json.loads(seen[0].content) == {
        "summary": "TuttiTrip: Gdańsk",
        "timeZone": "Europe/Warsaw",
    }


@run
async def test_an_existing_event_is_updated_not_duplicated() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"message": "exists"}})
        return httpx.Response(200, json={})

    http, _ = google_http(httpx.MockTransport(handler))
    await CalendarApi(http, GOOGLE_TOKEN).upsert_event(
        "cal", "abc123", {"summary": "x"}
    )
    assert calls == [
        ("POST", "/calendar/v3/calendars/cal/events"),
        ("PUT", "/calendar/v3/calendars/cal/events/abc123"),
    ]


@run
async def test_a_missing_calendar_is_reported() -> None:
    http, _ = google_http(status=404, body={"error": {}})
    with pytest.raises(GoogleNotFoundError):
        await CalendarApi(http, GOOGLE_TOKEN).upsert_event("cal", "abc123", {})


@run
async def test_deleting_a_gone_event_is_fine() -> None:
    http, _ = google_http(status=404, body={"error": {}})
    await CalendarApi(http, GOOGLE_TOKEN).delete_event("cal", "abc123")


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (
            401,
            {"error": {"message": "Invalid Credentials"}},
            GoogleErrorCode.TOKEN_EXPIRED,
        ),
        (
            403,
            {"error": {"message": "Request had insufficient authentication scopes."}},
            GoogleErrorCode.SCOPE_MISSING,
        ),
        (
            403,
            {"error": {"message": "Rate Limit Exceeded"}},
            GoogleErrorCode.UNAVAILABLE,
        ),
        (403, {"error": {"message": "Forbidden"}}, GoogleErrorCode.UNAVAILABLE),
        (500, {"error": {}}, GoogleErrorCode.UNAVAILABLE),
    ],
)
@run
async def test_google_refusals_become_codes_without_the_token(
    status: int, body: object, code: GoogleErrorCode, caplog: pytest.LogCaptureFixture
) -> None:
    http, _ = google_http(status=status, body=body)
    with caplog.at_level(logging.DEBUG), pytest.raises(GoogleAccessError) as caught:
        await CalendarApi(http, GOOGLE_TOKEN).create_calendar("x", "UTC")
    assert caught.value.code is code
    assert GOOGLE_TOKEN not in caught.value.message
    assert GOOGLE_TOKEN not in caplog.text


@run
async def test_a_network_failure_is_unavailable() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        message = "down"
        raise httpx.ConnectError(message)

    http, _ = google_http(httpx.MockTransport(boom))
    with pytest.raises(GoogleAccessError) as caught:
        await CalendarApi(http, GOOGLE_TOKEN).create_calendar("x", "UTC")
    assert caught.value.code is GoogleErrorCode.UNAVAILABLE


@run
async def test_drive_uploads_html_converted_to_a_google_doc() -> None:
    http, seen = google_http(body={"id": "f1", "webViewLink": "https://docs/f1"})
    uploaded = await DriveApi(http, GOOGLE_TOKEN).upsert_document(
        "Plan", "<h1>x</h1>", None
    )
    assert (uploaded.id, uploaded.web_view_link) == ("f1", "https://docs/f1")
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/upload/drive/v3/files"
    assert request.url.params["uploadType"] == "multipart"
    assert request.headers["content-type"].startswith("multipart/related")
    text = request.content.decode()
    assert '"mimeType": "application/vnd.google-apps.document"' in text
    assert "<h1>x</h1>" in text


@run
async def test_drive_replaces_the_content_of_the_earlier_file() -> None:
    http, seen = google_http(body={"id": "f1", "webViewLink": "https://docs/f1"})
    await DriveApi(http, GOOGLE_TOKEN).upsert_document("Plan", "<p>v2</p>", "f1")
    assert (seen[0].method, seen[0].url.path) == ("PATCH", "/upload/drive/v3/files/f1")


@run
async def test_drive_makes_a_new_file_when_the_old_one_is_gone() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "PATCH":
            return httpx.Response(404, json={"error": {}})
        return httpx.Response(200, json={"id": "f2", "webViewLink": "https://docs/f2"})

    http, _ = google_http(httpx.MockTransport(handler))
    uploaded = await DriveApi(http, GOOGLE_TOKEN).upsert_document("Plan", "<p/>", "f1")
    assert uploaded.id == "f2"
    assert calls == ["PATCH", "POST"]
