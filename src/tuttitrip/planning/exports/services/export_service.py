"""Save an approved plan in the caller's Google Calendar or on their Drive.

Both need the plan to be approved by every member (the file of that version is
what they signed) and the caller's Google token with the right scope. The
exports are recorded per user, so saving again updates the calendar or the
document instead of adding another one. No token is stored or logged.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.planning.exports import db
from tuttitrip.planning.exports.logic.calendar_events import build_events
from tuttitrip.planning.exports.logic.constants import (
    CALENDAR_PREFIX,
    CALENDAR_URL,
    KIND_CALENDAR,
    KIND_DRIVE,
)
from tuttitrip.planning.exports.logic.plan_html import build_html
from tuttitrip.planning.exports.schemas import CalendarExportRead, DriveExportRead
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.google.services.google_api import (
    CalendarApi,
    GoogleNotFoundError,
)
from tuttitrip.shared.google.services.google_token import GoogleAccess
from tuttitrip.trips.schemas import TripMembership


async def _remove(api: CalendarApi, calendar_id: str, ids: list[str]) -> None:
    for event_id in ids:
        await api.delete_event(calendar_id, event_id)


async def export_calendar(
    session: AsyncSession,
    membership: TripMembership,
    plan_id: UUID,
    google: GoogleAccess,
) -> CalendarExportRead:
    """Write the stops of an approved plan as events of a secondary calendar.

    The calendar "TuttiTrip: <trip>" is created on the first save and recorded
    at once, so a failure halfway never leaves a calendar nobody knows about. A
    later save (the same plan or a newer approved version) updates the events
    with the same ids and removes those the newer version no longer has. A
    calendar the user deleted is created again. No transaction is held while
    Google is called.

    Args:
        session: Open session.
        membership: The caller's membership of the trip.
        plan_id: The approved version.
        google: Google clients.

    Returns:
        What the calendar holds now.

    Raises:
        PlanNotFoundError: When the trip has no such version.
        PlanNotApprovedError: When the version has no approved proposal.
        GoogleAccessError: When Google cannot be used (consent, expiry, outage).
    """
    approved = await plan_service.approved_plan(session, membership, plan_id)
    events = build_events(approved.plan, approved.timezone)
    api = await google.calendar(membership.sub)
    row = await db.select_export(
        session, membership.trip_id, membership.sub, KIND_CALENDAR
    )
    calendar_id = None if row is None else row.external_id
    known = [] if row is None else list(row.event_ids)
    await session.rollback()
    created = calendar_id is None
    title = f"{CALENDAR_PREFIX} {approved.trip_name}"
    for attempt in range(2):  # the second one after a calendar deleted by the user
        if calendar_id is None:
            calendar_id = await api.create_calendar(title, approved.timezone)
            known = []
            await _save(
                session,
                membership,
                KIND_CALENDAR,
                {"external_id": calendar_id, "plan_id": plan_id},
            )
        try:
            for event in events:
                await api.upsert_event(calendar_id, event.id, event.body)
            break
        except GoogleNotFoundError:
            if attempt == 1:
                raise
            calendar_id, created = None, True
    assert calendar_id is not None  # ruff: ignore[assert] the loop returned or raised
    keep = {e.id for e in events}
    stale = [i for i in known if i not in keep]
    for event_id in stale:
        await api.delete_event(calendar_id, event_id)
    await _save(
        session,
        membership,
        KIND_CALENDAR,
        {"external_id": calendar_id, "plan_id": plan_id, "event_ids": list(keep)},
    )
    return CalendarExportRead(
        plan_id=plan_id,
        calendar_id=calendar_id,
        calendar_url=CALENDAR_URL,
        events=len(events),
        removed=len(stale),
        created=created,
    )


async def _save(
    session: AsyncSession,
    membership: TripMembership,
    kind: str,
    values: dict[str, object],
) -> None:
    await db.upsert_export(
        session,
        trip_id=membership.trip_id,
        user_sub=membership.sub,
        kind=kind,
        values={"event_ids": [], "web_link": None, **values},
    )
    await session.commit()


async def export_drive(
    session: AsyncSession,
    membership: TripMembership,
    plan_id: UUID,
    google: GoogleAccess,
) -> DriveExportRead:
    """Upload an approved plan as a Google Doc.

    The first export makes the document; a later one replaces its content.

    Args:
        session: Open session.
        membership: The caller's membership of the trip.
        plan_id: The approved version.
        google: Google clients.

    Returns:
        The file id and the link that opens it.

    Raises:
        PlanNotFoundError: When the trip has no such version.
        PlanNotApprovedError: When the version has no approved proposal.
        GoogleAccessError: When Google cannot be used (consent, expiry, outage).
    """
    approved = await plan_service.approved_plan(session, membership, plan_id)
    api = await google.drive(membership.sub)
    row = await db.select_export(
        session, membership.trip_id, membership.sub, KIND_DRIVE
    )
    earlier = None if row is None else row.external_id
    await session.rollback()
    uploaded = await api.upsert_document(
        f"{CALENDAR_PREFIX} {approved.trip_name}",
        build_html(approved.plan, approved.trip_name),
        earlier,
    )
    await _save(
        session,
        membership,
        KIND_DRIVE,
        {
            "external_id": uploaded.id,
            "plan_id": plan_id,
            "web_link": uploaded.web_view_link,
        },
    )
    return DriveExportRead(
        plan_id=plan_id,
        file_id=uploaded.id,
        web_view_link=uploaded.web_view_link,
        created=earlier != uploaded.id,
    )
