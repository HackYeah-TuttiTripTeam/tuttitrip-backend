"""Plan service: gathers the trip's data, runs the algorithm, stores versions.

The algorithm is pure and synchronous, so it runs in a worker thread
(``anyio.to_thread.run_sync``); the event loop stays free. The same input gives
the existing version back; parallel requests for one trip are serialised by an
advisory lock around the check-and-insert, so none ends in a 500.
"""

import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import partial
from uuid import UUID

import anyio.to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.places.schemas import PlaceRead
from tuttitrip.places.services import place_service
from tuttitrip.planning.logic.budget_consent import plan_with_consent
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.overrides import db as overrides_db
from tuttitrip.planning.plans import db
from tuttitrip.planning.plans.logic.input_builder import (
    ALGORITHM_VERSION,
    PlanInputError,
    build_input,
    input_hash,
)
from tuttitrip.planning.plans.logic.read_model import build_content
from tuttitrip.planning.plans.logic.verdict import build_verdicts
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.planning.plans.schemas import PlanCreate, PlanParams, PlanRead
from tuttitrip.planning.schemas import PlanningInput
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

PAGE = 200
_RESULT_KEYS = (
    "days",
    "lodging",
    "fairness",
    "floors_missed",
    "violation",
    "conflicts",
    "explain",
    "verdicts",
    "budget",
    "telemetry",
)


class PlanNotFoundError(Exception):
    """The trip has no such plan version."""


async def _read(
    session: AsyncSession, membership: TripMembership, row: PlanVersion
) -> PlanRead:
    """Build the response for the caller.

    The ledger (``u``, ``r``, the domains) is visible to every member. ``explain``
    carries each person's effort ``e_ip``, which depends on their stairs, walking
    and queue limits (health data), so a caller below co-host sees only their own
    cards.

    Args:
        session: Open session.
        membership: The caller's membership.
        row: The stored version.

    Returns:
        The plan as the caller may see it.
    """
    result = dict(row.result)
    if not membership.role.satisfies(TripRole.CO_HOST):
        own = await profile_service.find_account_profile(
            session, membership.trip_id, membership.sub
        )
        result["explain"] = [
            e
            for e in result["explain"]
            if own is not None and e["profile_id"] == str(own)
        ]
    return PlanRead.model_validate(
        {
            "id": row.id,
            "trip_id": row.trip_id,
            "version": row.version,
            "input_hash": row.input_hash,
            "plan_hash": row.plan_hash,
            "created_at": row.created_at,
            "params": PlanParams.model_validate(row.params),
            **{key: result[key] for key in _RESULT_KEYS},
        }
    )


async def _city_places(session: AsyncSession, slug: str) -> list[PlaceRead]:
    found: list[PlaceRead] = []
    while page := await place_service.list_places(
        session, slug, None, limit=PAGE, offset=len(found)
    ):
        found.extend(page)
        if len(page) < PAGE:
            break
    return found


async def gather_input(
    session: AsyncSession, caller: TripMembership
) -> tuple[PlanningInput, dict[UUID, str], float]:
    """The trip, its people, their feedback, the catalog and the overrides.

    The data is read with a host-level view of the trip (not scoped to the
    caller): the plan reads everybody's health data, so the result must not depend
    on who asks. What the caller may see is decided when the response is built.

    Args:
        session: Open session.
        caller: The caller's membership (any role).

    Returns:
        The algorithm input, display names by profile id and the trip's alpha.

    Raises:
        PlanInputError: When the trip lacks dates, a city or people.
    """
    membership = caller.model_copy(update={"role": TripRole.HOST})
    trip = await trip_service.get_trip(session, membership)
    if trip.city_slug is None:
        msg = "The trip needs a city to plan"
        raise PlanInputError(msg)
    cities = {c.slug: c for c in await place_service.list_cities(session)}
    if trip.city_slug not in cities:
        msg = f"Unknown city '{trip.city_slug}'"
        raise PlanInputError(msg)
    profiles = await profile_service.list_profiles(session, membership)
    preferences = await preference_service.list_preferences(session, membership)
    feedback = await feedback_service.list_for_trip(session, membership.trip_id)
    places = await _city_places(session, trip.city_slug)
    active = await overrides_db.select_active(session, membership.trip_id)
    planning = build_input(
        trip,
        city=cities[trip.city_slug],
        profiles=profiles,
        preferences=preferences,
        feedback=feedback,
        places=places,
        must=frozenset(o.place_id for o in active if o.kind == "must"),
        blocked=frozenset(o.place_id for o in active if o.kind == "block"),
    )
    names = {p.id: p.display_name for p in profiles}
    return planning, names, trip.fairness_alpha


@dataclass(frozen=True, slots=True)
class _Stored:
    plan_hash: str
    result: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Computed(_Stored):
    alternative: _Stored | None


def _compute(
    planning: PlanningInput,
    alpha: float,
    names: Mapping[UUID, str],
    alternative_id: UUID,
) -> _Computed:
    # Runs in a worker thread: N solo runs, the group plan and, when the plan goes
    # over B_do, P_strict and the cheaper alternative (E6).
    started = time.perf_counter()
    decision = plan_with_consent(planning, DEFAULT_PARAMS, alpha=alpha)
    chosen = decision.chosen
    verdicts = build_verdicts(planning, chosen.plan.place_ids)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    alternative = None
    if decision.needs_approval and decision.alternative is not None:
        strict = decision.alternative
        alternative = _Stored(
            strict.plan.plan_hash,
            build_content(
                planning,
                strict,
                names,
                verdicts=build_verdicts(planning, strict.plan.place_ids),
                elapsed_ms=elapsed_ms,
            ),
        )
    content = build_content(
        planning,
        chosen,
        names,
        decision=decision,
        strict_plan_id=alternative_id,
        verdicts=verdicts,
        elapsed_ms=elapsed_ms,
    )
    return _Computed(chosen.plan.plan_hash, content, alternative)


async def generate_plan(
    session: AsyncSession, membership: TripMembership, data: PlanCreate | None
) -> tuple[PlanRead, bool]:
    """Compute a plan for the trip, or return the latest version of the same input.

    Any member may ask (a member's veto triggers the recompute). The input is
    gathered with a host-level view, so the same data gives the same plan whoever
    asks; the response hides what the caller may not see (see ``_read``).

    Only the LATEST version is reused: if the input went back to an older state
    (a veto and its removal), a new version is stored so that "latest" is always
    the plan of the current input.

    Args:
        session: Open session.
        membership: The caller's membership (any role).
        data: Knobs of the request, or None for the trip's own ``alpha``.

    Returns:
        The plan and whether a new version was stored.

    Raises:
        PlanInputError: When the trip lacks dates, a city or people.
    """
    planning, names, trip_alpha = await gather_input(session, membership)
    alpha = trip_alpha if data is None or data.alpha is None else data.alpha
    preset = (data or PlanCreate()).weight_preset
    digest = input_hash(planning, alpha, preset.value, DEFAULT_PARAMS)

    latest = await db.select_latest(session, membership.trip_id)
    if latest is not None and latest.input_hash == digest:
        return await _read(session, membership, latest), False
    await session.rollback()  # do not hold a transaction while computing

    alternative_id = uuid.uuid4()
    computed = await anyio.to_thread.run_sync(
        partial(_compute, planning, alpha, names, alternative_id)
    )

    await db.lock_trip_plans(session, membership.trip_id)
    latest = await db.select_latest(session, membership.trip_id)
    if latest is not None and latest.input_hash == digest:
        return await _read(session, membership, latest), False  # a parallel request
    row = PlanVersion(
        trip_id=membership.trip_id,
        version=(latest.version if latest else 0) + 1,
        input_hash=digest,
        plan_hash=computed.plan_hash,
        params={
            "alpha": alpha,
            "weight_preset": preset.value,
            "algorithm_version": ALGORITHM_VERSION,
            "algorithm": asdict(DEFAULT_PARAMS),
        },
        result=computed.result,
        created_by_sub=membership.sub,
    )
    session.add(row)
    if computed.alternative is not None:
        await session.flush()
        session.add(
            PlanVersion(
                id=alternative_id,
                trip_id=membership.trip_id,
                version=row.version,
                input_hash=digest,
                plan_hash=computed.alternative.plan_hash,
                params=row.params,
                result=computed.alternative.result,
                created_by_sub=membership.sub,
                alternative_of=row.id,
            )
        )
    await session.commit()
    await session.refresh(row)
    return await _read(session, membership, row), True


async def latest_plan(session: AsyncSession, membership: TripMembership) -> PlanRead:
    """Newest version of the trip's plan.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.

    Returns:
        The plan.

    Raises:
        PlanNotFoundError: When the trip has no plan yet.
    """
    row = await db.select_latest(session, membership.trip_id)
    if row is None:
        raise PlanNotFoundError(str(membership.trip_id))
    return await _read(session, membership, row)


async def get_plan(
    session: AsyncSession, membership: TripMembership, plan_id: UUID
) -> PlanRead:
    """One stored version.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        plan_id: Version id.

    Returns:
        The plan.

    Raises:
        PlanNotFoundError: When the trip has no such version.
    """
    row = await db.select_by_id(session, membership.trip_id, plan_id)
    if row is None:
        raise PlanNotFoundError(str(plan_id))
    return await _read(session, membership, row)
