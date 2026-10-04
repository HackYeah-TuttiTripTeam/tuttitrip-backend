"""Fixed values of the Google integration."""

from typing import Final

GOOGLE_IDENTITY_PROVIDER: Final = "google-oauth2"
"""Provider name of the Google login in the Auth0 ``identities`` array."""

SCOPE_CALENDAR_APP_CREATED: Final = (
    "https://www.googleapis.com/auth/calendar.app.created"
)
"""Secondary calendars created by the app and the events in them."""

SCOPE_DRIVE_FILE: Final = "https://www.googleapis.com/auth/drive.file"
"""Only the files the app created or the user opened with it."""

CALENDAR_API_URL: Final = "https://www.googleapis.com/calendar/v3"
DRIVE_UPLOAD_URL: Final = "https://www.googleapis.com/upload/drive/v3"

GOOGLE_DOC_MIME: Final = "application/vnd.google-apps.document"
"""Uploading HTML with this target type makes Drive convert it to a Google Doc."""
HTML_MIME: Final = "text/html; charset=UTF-8"

HTTP_TIMEOUT_SECONDS: Final = 15.0
"""Per call to a Google API."""

USER_AGENT: Final = "TuttiTripBackend/1.0 (+https://tuttitrip.gburek.app)"

INSUFFICIENT_SCOPE_MARKER: Final = "insufficient"
"""Substring of Google's 403 for a token without the needed scope."""
