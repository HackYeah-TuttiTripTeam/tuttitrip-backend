"""Fixed values of the Google exports."""

from typing import Final
from uuid import NAMESPACE_DNS, UUID, uuid5

CALENDAR_PREFIX: Final = "TuttiTrip:"
"""Title prefix of the secondary calendar and of the Drive document."""

EVENT_ID_NAMESPACE: Final[UUID] = uuid5(NAMESPACE_DNS, "calendar.tuttitrip.gburek.app")
"""Namespace of the event ids: the id of a stop is a UUIDv5 of the trip and place."""

KIND_CALENDAR: Final = "calendar"
KIND_DRIVE: Final = "drive"

CALENDAR_URL: Final = "https://calendar.google.com/calendar/u/0/r"
"""Where the user finds the calendar (it is listed under "My calendars")."""
