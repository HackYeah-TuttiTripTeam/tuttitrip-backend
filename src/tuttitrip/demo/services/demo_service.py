"""Reset the demo account to its sample data.

Everything goes through the services of the other domains, so the rules
(defaults of an age group, weights, preferences) apply as for a real user.
The reset deletes every trip the demo account owns and creates the set again,
so it can run any number of times (at deploy, daily, before a presentation).
"""

import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tuttitrip.accommodation.schemas import RequirementItem, RequirementsWrite
from tuttitrip.accommodation.services import requirements_service
from tuttitrip.demo.logic.dataset import DEMO_TRIPS, PersonSeed, TripSeed
from tuttitrip.places.services import place_service
from tuttitrip.planning.linter.schemas import DocumentCreate, DocumentKind
from tuttitrip.planning.linter.services import document_service
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.planning.plans.services.plan_service import PlanInputError
from tuttitrip.profiles.feedback.schemas import RatingUpdate, RatingValue, ReasonCode
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.schemas import (
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    WeightsUpdate,
)
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

log = logging.getLogger(__name__)

RESET_LOCK_KEY = 0x7475747469_64656D  # "tuttidem": a bigint key, only for this reset
RATED_PLACES = 20  # how many of a city's catalog places ratings are picked from


async def _rate_places(
    session: AsyncSession,
    membership: TripMembership,
    people: list[tuple[PersonSeed, ProfileRead]],
    city_slug: str,
) -> int:
    """Give every person a few thumbs on the city's catalog places, if any.

    Args:
        session: Open session.
        membership: The host's membership of the trip.
        people: Each seed with its created profile.
        city_slug: The city whose catalog places are rated.

    Returns:
        How many ratings were stored.
    """
    places = await place_service.list_places(
        session, city_slug, None, limit=RATED_PLACES, offset=0
    )
    rated = 0
    for index, (seed, profile) in enumerate(people):
        # Disjoint slices, shifted per person so the group disagrees a little.
        first = index
        middle = first + seed.wants
        wants = places[first:middle]
        dont_wants = places[middle : middle + seed.dont_wants]
        for place, value, reason in (
            *((p, RatingValue.WANT, None) for p in wants),
            *((p, RatingValue.DONT_WANT, ReasonCode.NOT_MY_STYLE) for p in dont_wants),
        ):
            await feedback_service.rate_place(
                session,
                membership,
                profile.id,
                place.id,
                RatingUpdate(value=value, reason_code=reason),
            )
            rated += 1
    return rated


async def _plan_and_documents(
    session: AsyncSession, membership: TripMembership, seed: TripSeed, today: date
) -> str | None:
    """Compute the plan and store the pasted chatbot plan and lodging offer.

    The pasted texts are stored even when the plan cannot be computed (a
    database without the city's catalog), so the linter scenes still work.

    Args:
        session: Open session.
        membership: The host's membership of the trip.
        seed: The trip's sample data.
        today: The day of the reset.

    Returns:
        The 12-character ``plan_hash`` of the stored plan, or None without a plan.
    """
    text = seed.chatbot_text(today)
    if text is not None:
        await document_service.create_document(
            session, membership, DocumentCreate(kind=DocumentKind.PLAN, text=text)
        )
    if seed.offer_text is not None:
        await document_service.create_document(
            session,
            membership,
            DocumentCreate(kind=DocumentKind.OFFER, text=seed.offer_text),
        )
    if not seed.plan:
        return None
    try:
        plan, _ = await plan_service.generate_plan(session, membership, None)
    except PlanInputError as exc:
        log.warning("Demo plan of '%s' not computed: %s", seed.name, exc)
        return None
    return plan.plan_hash


async def _create_trip(
    session: AsyncSession, sub: str, seed: TripSeed, today: date
) -> str | None:
    trip = await trip_service.create_trip(session, sub, seed.create_payload(today))
    membership = await trip_service.get_membership(session, trip.id, sub, TripRole.HOST)
    host_profile, *_ = await profile_service.list_profiles(session, membership)
    host, *others = seed.people
    profiles = [
        await profile_service.update_profile(
            session,
            membership,
            host_profile.id,
            ProfileUpdate(display_name=host.name, age=host.age),
        )
    ]
    for person in others:
        created = await profile_service.create_profile(
            session, membership, ProfileCreate(display_name=person.name, age=person.age)
        )
        profiles.append(created)
    people = list(zip(seed.people, profiles, strict=True))
    for person, profile in people:
        await preference_service.replace_preferences(
            session, membership, profile.id, person.preferences()
        )
    await _rate_places(session, membership, people, seed.city_slug)
    if seed.hard_amenities:
        await requirements_service.replace_requirements(
            session,
            membership,
            RequirementsWrite(
                requirements=[
                    RequirementItem.model_validate(
                        {"kind": "amenity", "key": a.value, "hard": True}
                    )
                    for a in seed.hard_amenities
                ]
            ),
        )
    if seed.weights is not None and seed.focus is not None:
        await profile_service.set_weights(
            session,
            membership,
            WeightsUpdate(
                preset=seed.weights, focus_profile_id=profiles[seed.focus].id
            ),
        )
    return await _plan_and_documents(session, membership, seed, today)


async def reset_demo_account(
    session: AsyncSession, sub: str, today: date | None = None
) -> int:
    """Replace the account's own trips with the sample set.

    Only trips with ``owner_sub == sub`` are deleted; trips the account merely
    joined stay. The services commit, so call it on a session joined to an
    outer transaction (see ``run_reset``) to make the whole reset atomic.

    Args:
        session: Open session.
        sub: Auth0 subject of the demo account.
        today: The day dates are counted from (tests); defaults to today.

    Returns:
        The number of trips created.
    """
    today = today or date.today()  # ruff: ignore[call-date-today]  # a calendar day, not an instant
    removed = await trip_service.delete_trips_owned_by(session, sub)
    for seed in DEMO_TRIPS:
        plan_hash = await _create_trip(session, sub, seed, today)
        if plan_hash is not None:
            log.info("Demo trip '%s': plan_hash %s", seed.name, plan_hash)
    log.info(
        "Demo account reset: %d trips removed, %d created", removed, len(DEMO_TRIPS)
    )
    return len(DEMO_TRIPS)


async def run_reset(engine: AsyncEngine, sub: str, today: date | None = None) -> int:
    """Reset the demo account in one transaction, one reset at a time.

    A Postgres advisory lock serializes concurrent resets (deploy, schedule,
    manual). The services' commits only release savepoints of the outer
    transaction, so a failure anywhere rolls everything back and the previous
    data stays.

    Args:
        engine: Database engine.
        sub: Auth0 subject of the demo account.
        today: The day dates are counted from (tests); defaults to today.

    Returns:
        The number of trips created.
    """
    async with engine.connect() as connection, connection.begin():
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": RESET_LOCK_KEY}
        )
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            return await reset_demo_account(session, sub, today)
        finally:
            await session.close()
