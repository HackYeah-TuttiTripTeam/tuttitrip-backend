"""The days ahead with their weekday, so that relative dates are code, not guesses."""

from datetime import date, timedelta

from tuttitrip.interview import constants


def upcoming_days(today: date, count: int = constants.CALENDAR_DAYS) -> list[str]:
    """List the next days with their Polish weekday names.

    Args:
        today: The current date; it is the first entry.
        count: How many days to list.

    Returns:
        Lines such as ``"sobota 2026-10-10"``, starting with ``today``.
    """
    days = [today + timedelta(days=n) for n in range(count)]
    return [f"{constants.WEEKDAYS_PL[d.weekday()]} {d.isoformat()}" for d in days]
