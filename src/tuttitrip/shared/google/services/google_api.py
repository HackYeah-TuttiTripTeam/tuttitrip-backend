"""Calendar and Drive calls with a user's access token (httpx).

Only the calls TuttiTrip needs. Google's answers become ``GoogleAccessError``:
401 is an expired token, 403 about scopes a missing scope, anything else that
is not a success is ``unavailable``. The token travels only in the
``Authorization`` header and never appears in a message.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import quote

import httpx

from tuttitrip.shared.google.constants import (
    CALENDAR_API_URL,
    DRIVE_UPLOAD_URL,
    GOOGLE_DOC_MIME,
    HTML_MIME,
    HTTP_TIMEOUT_SECONDS,
    INSUFFICIENT_SCOPE_MARKER,
    USER_AGENT,
)
from tuttitrip.shared.google.schemas import GoogleAccessError, GoogleErrorCode

_BOUNDARY = "tuttitrip-multipart"
_EXPIRED = "Google rejected the access token"
_SCOPE = "The Google token lacks the scope for this call"
_FAILED = "Google API call failed"


class GoogleNotFoundError(Exception):
    """Google answered 404: the calendar, event or file does not exist (any more)."""


class GoogleConflictError(Exception):
    """Google answered 409: the resource with this id already exists."""


def build_client() -> httpx.AsyncClient:
    """Create the HTTP client used for Google calls.

    Returns:
        A client with the project's user agent and a timeout.
    """
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
    )


def _fail(response: httpx.Response) -> Exception:
    status = response.status_code
    if status == httpx.codes.NOT_FOUND:
        return GoogleNotFoundError()
    if status == httpx.codes.CONFLICT:
        return GoogleConflictError()
    if status == httpx.codes.UNAUTHORIZED:
        return GoogleAccessError(GoogleErrorCode.TOKEN_EXPIRED, _EXPIRED)
    if status == httpx.codes.FORBIDDEN and (
        INSUFFICIENT_SCOPE_MARKER in response.text.lower()
    ):
        return GoogleAccessError(GoogleErrorCode.SCOPE_MISSING, _SCOPE)
    return GoogleAccessError(GoogleErrorCode.UNAVAILABLE, f"{_FAILED}: {status}")


@dataclass(frozen=True, slots=True)
class _Body:
    """What goes in a request besides the method and the URL."""

    json: dict[str, Any] | None = None
    content: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Google:
    http: httpx.AsyncClient
    token: str

    async def _call(
        self, method: str, url: str, body: _Body | None = None
    ) -> dict[str, Any]:
        sent = body or _Body()
        try:
            response = await self.http.request(
                method,
                url,
                json=sent.json,
                content=sent.content,
                params=sent.params,
                headers={"Authorization": f"Bearer {self.token}", **sent.headers},
            )
        except httpx.HTTPError as exc:
            raise GoogleAccessError(GoogleErrorCode.UNAVAILABLE, _FAILED) from exc
        if not response.is_success:
            raise _fail(response)
        if not response.content:
            return {}
        try:
            parsed = response.json()
        except ValueError as exc:
            raise GoogleAccessError(GoogleErrorCode.UNAVAILABLE, _FAILED) from exc
        return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


class CalendarApi(_Google):
    """Google Calendar v3: a secondary calendar and its events."""

    async def create_calendar(self, summary: str, time_zone: str) -> str:
        """Create a secondary calendar.

        Args:
            summary: Calendar title.
            time_zone: IANA zone of the calendar.

        Returns:
            The calendar id.

        Raises:
            GoogleAccessError: The call was refused or failed.
        """
        body = await self._call(
            "POST",
            f"{CALENDAR_API_URL}/calendars",
            _Body(json={"summary": summary, "timeZone": time_zone}),
        )
        return str(body["id"])

    async def upsert_event(
        self, calendar_id: str, event_id: str, event: dict[str, Any]
    ) -> None:
        """Insert an event with a fixed id, or update it when it exists.

        Args:
            calendar_id: Calendar id.
            event_id: Id chosen by the caller (base32hex characters).
            event: Event resource without the id.

        Raises:
            GoogleNotFoundError: The calendar does not exist (any more).
            GoogleAccessError: The call was refused or failed.
        """
        url = f"{CALENDAR_API_URL}/calendars/{quote(calendar_id, safe='')}/events"
        try:
            await self._call("POST", url, _Body(json={**event, "id": event_id}))
        except GoogleConflictError:
            await self._call("PUT", f"{url}/{event_id}", _Body(json=event))

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Delete an event; one that is already gone is fine.

        Args:
            calendar_id: Calendar id.
            event_id: Event id.

        Raises:
            GoogleAccessError: The call was refused or failed.
        """
        url = (
            f"{CALENDAR_API_URL}/calendars/{quote(calendar_id, safe='')}"
            f"/events/{event_id}"
        )
        try:
            await self._call("DELETE", url)
        except GoogleNotFoundError:
            return


@dataclass(frozen=True, slots=True)
class DriveFile:
    """A file on Drive."""

    id: str
    web_view_link: str


class DriveApi(_Google):
    """Google Drive v3: one converted document."""

    async def _upload(
        self, method: str, url: str, metadata: dict[str, Any], html: str
    ) -> DriveFile:
        payload = (
            f"--{_BOUNDARY}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{_BOUNDARY}\r\nContent-Type: {HTML_MIME}\r\n\r\n{html}\r\n"
            f"--{_BOUNDARY}--"
        )
        body = await self._call(
            method,
            url,
            _Body(
                content=payload.encode(),
                headers={"Content-Type": f"multipart/related; boundary={_BOUNDARY}"},
                params={"uploadType": "multipart", "fields": "id,webViewLink"},
            ),
        )
        return DriveFile(id=str(body["id"]), web_view_link=str(body["webViewLink"]))

    async def upsert_document(
        self, name: str, html: str, file_id: str | None
    ) -> DriveFile:
        """Upload HTML as a Google Doc, replacing the earlier upload when it exists.

        Args:
            name: File name on Drive.
            html: Document body; Drive converts it to a Google Doc.
            file_id: Id of the file made by an earlier export, if any.

        Returns:
            The file id and the link to open it.

        Raises:
            GoogleAccessError: The call was refused or failed.
        """
        if file_id is not None:
            try:
                return await self._upload(
                    "PATCH",
                    f"{DRIVE_UPLOAD_URL}/files/{quote(file_id, safe='')}",
                    {"name": name},
                    html,
                )
            except GoogleNotFoundError:
                pass  # deleted on Drive: make a new one
        return await self._upload(
            "POST",
            f"{DRIVE_UPLOAD_URL}/files",
            {"name": name, "mimeType": GOOGLE_DOC_MIME},
            html,
        )
