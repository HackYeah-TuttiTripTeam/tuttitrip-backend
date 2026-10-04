"""Plan service: gathers the trip's data, runs the algorithm, stores versions.

The algorithm is pure and synchronous, so it runs in a worker thread
(``anyio.to_thread.run_sync``); the event loop stays free. The same input gives
the existing version back; parallel requests for one trip are serialised by an
advisory lock around the check-and-insert, so none ends in a 500.
"""

import contextlib
import copy
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from functools import partial
from typing import Any
from uuid import UUID

import anyio.to_thread
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.accommodation.services import requirements_service
from tuttitrip.places.services import place_service
from tuttitrip.planning.anyway.logic.suggest import suggest
from tuttitrip.planning.anyway.services import anyway_service
from tuttitrip.planning.budget_approvals.services import approval_service
from tuttitrip.planning.logic import what_if
from tuttitrip.planning.logic.budget_consent import plan_with_consent
from tuttitrip.planning.logic.params import AlgorithmParams
from tuttitrip.planning.logic.progress import (
    PLAN_STEPS,
    PlanProgress,
    PlanStep,
    ProgressSink,
)
from tuttitrip.planning.logic.upgrades import find_upgrades
from tuttitrip.planning.overrides import db as overrides_db
from tuttitrip.planning.parameters.services import parameters_service
from tuttitrip.planning.plans import db
from tuttitrip.planning.plans.logic.ics import build_ics
from tuttitrip.planning.plans.logic.input_builder import (
    ALGORITHM_VERSION,
    MissingInputsError,
    PlanInputError,
    build_input,
    find_missing,
    input_hash,
    lodging_options,
)
from tuttitrip.planning.plans.logic.justification import template_justification
from tuttitrip.planning.plans.logic.read_model import build_content
from tuttitrip.planning.plans.logic.stored import stored_plan
from tuttitrip.planning.plans.logic.verdict import build_verdicts
from tuttitrip.planning.plans.models import PlanVersion
from tuttitrip.planning.plans.schemas import (
    ApprovalStatus,
    PlanAssumptions,
    PlanCreate,
    PlanProgressRead,
    PlanRead,
    VerdictKind,
)
from tuttitrip.planning.plans.services import plan_progress
from tuttitrip.planning.proposals.services import proposal_service
from tuttitrip.planning.schemas import PlanningInput, WhatIfTarget
from tuttitrip.planning.services.solver_service import configured_solver
from tuttitrip.profiles.feedback.services import feedback_service
from tuttitrip.profiles.preferences.schemas import PreferencesRead
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.jobs.contracts import (
    Locale,
    Workflow,
    WriteJustificationsInput,
    WriteJustificationsOutput,
)
from tuttitrip.shared.jobs.services.job_queue import (
    JobNotFoundError,
    JobQueue,
    JobQueueUnavailableError,
    workflow_id_for,
)
from tuttitrip.shared.jobs.services.worker_liveness import (
    WorkerUnavailableError,
    ensure_worker_available,
)
from tuttitrip.trips.schemas import TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service

CALENDAR_PREFIX = "TuttiTrip:"
FILE_PREFIX = "tuttitrip-plan"

log = logging.getLogger(__name__)

_JUSTIFICATIONS_KEY = "justifications"
_RUNNING = frozenset({"ENQUEUED", "DELAYED", "PENDING"})


class PlanNotFoundError(Exception):
    """The trip has no such plan version."""


class CatalogMissingError(PlanInputError):
    """The trip's city has no places in the catalog, so there is nothing to plan."""

    def __init__(self, city_slug: str) -> None:
        super().__init__(city_slug)
        self.city_slug = city_slug


def _justification_payload(plan_id: UUID, locale: Locale) -> WriteJustificationsInput:
    return WriteJustificationsInput(plan_id=plan_id, locale=locale)


def _justification_job_id(plan_id: UUID, locale: Locale) -> str:
    return workflow_id_for(
        Workflow.WRITE_JUSTIFICATIONS,
        str(plan_id),
        _justification_payload(plan_id, locale),
    )


async def _model_justifications(
    session: AsyncSession,
    queue: JobQueue | None,
    row: PlanVersion,
    locale: Locale,
    *,
    latest: bool,
) -> dict[str, str]:
    """Texts the worker wrote for this version, by place id.

    Only the newest version is asked: the first read after the job succeeds
    keeps its texts in the version's result, so later reads do not need the job
    system. An older version keeps what it had and never gets a newer text.

    Returns:
        The texts, empty while the job runs or when it failed.
    """
    stored = row.result.get(_JUSTIFICATIONS_KEY, {})
    if locale in stored:
        return dict(stored[locale])
    if not latest or queue is None:
        return {}
    try:
        job = await queue.get(_justification_job_id(row.id, locale))
    except JobNotFoundError, JobQueueUnavailableError:
        return {}
    if job.status != "SUCCESS":
        return {}
    try:
        output = WriteJustificationsOutput.model_validate(job.output or {})
    except ValidationError:
        return {}
    texts = {j.place_id: j.text for j in output.justifications if j.profile_id is None}
    row.result = {**row.result, _JUSTIFICATIONS_KEY: {**stored, locale: texts}}
    await session.commit()
    return texts


def _with_justifications(
    result: dict[str, object], texts: Mapping[str, str], locale: Locale
) -> None:
    # The template in the requested language, or the model's text when there is one.
    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list):
        return
    for verdict in verdicts:
        text = texts.get(str(verdict["place_id"]))
        verdict["justification_source"] = "model" if text else "template"
        verdict["justification"] = text or template_justification(
            VerdictKind(verdict["verdict"]),
            len(verdict["yes"]),
            len(verdict["no"]),
            verdict["skip_codes"],
            locale,
        )


async def _read(  # ruff: ignore[too-many-arguments] the caller's view of one version
    session: AsyncSession,
    queue: JobQueue | None,
    membership: TripMembership,
    row: PlanVersion,
    locale: Locale,
    *,
    latest: bool,
) -> PlanRead:
    """Build the response for the caller.

    The ledger (``u``, ``r``, the domains) is visible to every member. ``explain``
    carries each person's effort ``e_ip``, which depends on their stairs, walking
    and queue limits (health data), so a caller below co-host sees only their own
    cards.

    Every verdict carries a justification in ``locale``: the template, or the
    worker's text when the newest version has one.

    Args:
        session: Open session.
        queue: Job queue (justifications of the newest version).
        membership: The caller's membership.
        row: The stored version.
        locale: Language of the justifications.
        latest: Whether ``row`` is the trip's newest version.

    Returns:
        The plan as the caller may see it.
    """
    result = copy.deepcopy(row.result)
    texts = await _model_justifications(session, queue, row, locale, latest=latest)
    _with_justifications(result, texts, locale)
    if not membership.role.satisfies(TripRole.CO_HOST):
        own = await profile_service.find_account_profile(
            session, membership.trip_id, membership.sub
        )
        result["explain"] = [
            e
            for e in result["explain"]
            if own is not None and e["profile_id"] == str(own)
        ]
    plan = _stored(row, result)
    shown = await anyway_service.visible(session, row.trip_id, row.id, plan.anyway)
    plan = plan.model_copy(update={"anyway": shown})
    if plan.budget.needs_approval:
        decided = await approval_service.status_of_plan(session, row.id)
        if decided is not None:
            budget = plan.budget.model_copy(
                update={"approval_status": ApprovalStatus(decided.value)}
            )
            plan = plan.model_copy(update={"budget": budget})
    return plan


def _stored(row: PlanVersion, result: dict[str, Any] | None = None) -> PlanRead:
    return stored_plan(
        plan_id=row.id,
        trip_id=row.trip_id,
        version=row.version,
        input_hash=row.input_hash,
        plan_hash=row.plan_hash,
        created_at=row.created_at,
        params=row.params,
        result=row.result if result is None else result,
    )


def _assume(
    trip: TripRead,
    profiles: list[ProfileRead],
    preferences: list[PreferencesRead],
    assumptions: PlanAssumptions,
) -> tuple[TripRead, list[ProfileRead], list[PreferencesRead]]:
    # Fills only gaps: a date or a person the trip has is never replaced.
    trip = trip.model_copy(
        update={
            "start_date": trip.start_date or assumptions.start_date,
            "end_date": trip.end_date or assumptions.end_date,
        }
    )
    missing = assumptions.min_people - len(profiles)
    if missing > 0:
        extra = profile_service.assumed_adults(trip.id, len(profiles) + 1, missing)
        profiles = [*profiles, *extra]
        preferences = [*preferences, *preference_service.assumed_preferences(extra)]
    return trip, profiles, preferences


async def gather_input(
    session: AsyncSession,
    caller: TripMembership,
    assumptions: PlanAssumptions | None = None,
) -> tuple[PlanningInput, dict[UUID, str], float]:
    """The trip, its people, their feedback, the catalog and the overrides.

    The data is read with a host-level view of the trip (not scoped to the
    caller): the plan reads everybody's health data, so the result must not depend
    on who asks. What the caller may see is decided when the response is built.

    Args:
        session: Open session.
        caller: The caller's membership (any role).
        assumptions: Gaps to fill in memory for a draft plan, or None.

    Returns:
        The algorithm input, display names by profile id and the trip's alpha.

    Raises:
        MissingInputsError: When the trip lacks dates, a city or people.
        PlanInputError: When the destination has no city slug yet.
        CatalogMissingError: When the city has no places in the catalog.
    """
    membership = caller.model_copy(update={"role": TripRole.HOST})
    trip = await trip_service.fill_city_slug(session, membership)
    cities = {c.slug: c for c in await place_service.list_cities(session)}
    profiles = await profile_service.list_profiles(session, membership)
    preferences = await preference_service.list_preferences(session, membership)
    if assumptions is not None:
        trip, profiles, preferences = _assume(trip, profiles, preferences, assumptions)
    slug = trip.city_slug
    # A city outside the catalog is not missing: its places are fetched instead.
    missing = find_missing(
        trip, city_known=bool(slug or trip.destination), people=len(profiles)
    )
    if missing:
        raise MissingInputsError(missing)
    if slug is None:  # a destination the trip has no catalog slug for yet
        msg = "The trip needs a city to plan"
        raise PlanInputError(msg)
    feedback = await feedback_service.list_for_trip(session, membership.trip_id)
    places = await place_service.list_all_places(session, slug)
    if not places or slug not in cities:
        raise CatalogMissingError(slug)
    active = await overrides_db.select_active(session, membership.trip_id)
    required = await requirements_service.get_requirements(session, membership.trip_id)
    planning = build_input(
        trip,
        city=cities[slug],
        profiles=profiles,
        preferences=preferences,
        feedback=feedback,
        places=places,
        must=frozenset(o.place_id for o in active if o.kind == "must"),
        blocked=frozenset(o.place_id for o in active if o.kind == "block"),
        fares=await place_service.list_fares(session, slug),
        lodgings=lodging_options(
            places,
            required.requirements,
            trip.currency or cities[slug].currency,
        ),
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


def _compute(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] the inputs of one computation
    planning: PlanningInput,
    params: AlgorithmParams,
    alpha: float,
    names: Mapping[UUID, str],
    alternative_id: UUID,
    rejected: frozenset[tuple[int, UUID]],
    progress: ProgressSink,
) -> _Computed:
    # Runs in a worker thread: N solo runs, the group plan and, when the plan goes
    # over B_do, P_strict and the cheaper alternative (E6).
    started = time.perf_counter()
    progress(PlanProgress(PlanStep.CATALOGUE))
    decision = plan_with_consent(
        planning,
        params,
        alpha=alpha,
        solver=configured_solver().solver,
        progress=progress,
    )
    progress(PlanProgress(PlanStep.VERDICTS))
    chosen = decision.chosen
    verdicts = build_verdicts(planning, chosen.plan.place_ids)
    upgrades = find_upgrades(planning, chosen, params, alpha=alpha)
    anyway = suggest(
        planning,
        chosen,
        verdicts,
        alpha=alpha,
        rejected=rejected,
        params=params,
        solver=configured_solver().solver,
    )
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
        upgrades=upgrades,
        anyway=anyway,
        elapsed_ms=elapsed_ms,
    )
    return _Computed(chosen.plan.plan_hash, content, alternative)


def _is_current(latest: PlanVersion, digest: str, *, draft: bool) -> bool:
    # The same input is reused only as the same kind of plan: a draft that the
    # complete data later reproduces becomes a regular version of its own.
    return (
        latest.input_hash == digest and bool(latest.params.get("draft", False)) is draft
    )


async def _start_justifications(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] ids of one version
    session: AsyncSession,
    queue: JobQueue,
    sub: str,
    plan_id: UUID,
    locale: Locale,
    previous_id: UUID | None,
) -> None:
    """Ask the worker for the texts of the new version; never fails the save.

    The job of the version it replaces is cancelled, so the ``local_llm`` queue
    (two at a time) holds only the newest plan. A missing worker or queue just
    leaves the templates.
    """
    if previous_id is not None:
        with contextlib.suppress(JobNotFoundError, JobQueueUnavailableError):
            await queue.cancel(_justification_job_id(previous_id, locale))
    try:
        await ensure_worker_available(session)
        await queue.enqueue(
            Workflow.WRITE_JUSTIFICATIONS,
            _justification_payload(plan_id, locale),
            user=sub,
            key=str(plan_id),
        )
    except WorkerUnavailableError, JobQueueUnavailableError:
        log.info("justifications of plan %s not started: no worker", plan_id)


async def generate_plan(  # ruff: ignore[too-many-locals] compute, lock, store, announce
    session: AsyncSession,
    membership: TripMembership,
    data: PlanCreate | None,
    assumptions: PlanAssumptions | None = None,
    *,
    queue: JobQueue | None = None,
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
        assumptions: Gaps to fill in memory for a preliminary (draft) plan, or
            None for a plan of the trip as it is. The version is marked ``draft``.
        queue: Job queue for the verdict justifications; None (a draft inside
            an interview) leaves the templates and starts no job.

    Returns:
        The plan and whether a new version was stored.

    Raises:
        PlanInputError: When the trip lacks dates, a city or people.
        CatalogMissingError: When the city has no places in the catalog.
    """
    draft = assumptions is not None
    planning, names, trip_alpha = await gather_input(session, membership, assumptions)
    alpha = trip_alpha if data is None or data.alpha is None else data.alpha
    knobs = data or PlanCreate()
    version, current = await parameters_service.current(session)
    params = replace(current, max_exceptional_nights=knobs.exceptional_nights)
    digest = input_hash(
        planning,
        alpha,
        knobs.weight_preset.value,
        params,
        configured_solver().tag,
        parameters_version=version,
    )
    locale = knobs.locale

    latest = await db.select_latest(session, membership.trip_id)
    if latest is not None and _is_current(latest, digest, draft=draft):
        return await _read(
            session, queue, membership, latest, locale, latest=True
        ), False
    rejected = await anyway_service.rejected_pairs(session, membership.trip_id)
    await session.rollback()  # do not hold a transaction while computing

    alternative_id = uuid.uuid4()
    with plan_progress.track(membership.trip_id) as progress:
        computed = await anyio.to_thread.run_sync(
            partial(
                _compute,
                planning,
                params,
                alpha,
                names,
                alternative_id,
                rejected,
                progress,
            )
        )

    await db.lock_trip_plans(session, membership.trip_id)
    latest = await db.select_latest(session, membership.trip_id)
    if latest is not None and _is_current(latest, digest, draft=draft):
        return await _read(
            session, queue, membership, latest, locale, latest=True
        ), False  # a parallel request
    row = PlanVersion(
        trip_id=membership.trip_id,
        version=(latest.version if latest else 0) + 1,
        input_hash=digest,
        plan_hash=computed.plan_hash,
        params={
            "alpha": alpha,
            "weight_preset": knobs.weight_preset.value,
            "draft": draft,
            "algorithm_version": ALGORITHM_VERSION,
            "parameters_version": version,
            "algorithm": asdict(params),
        },
        result=computed.result,
        created_by_sub=membership.sub,
    )
    # The plan changed: what was proposed or asked about the old one is obsolete.
    await proposal_service.on_plan_changed(session, membership.trip_id)
    await approval_service.on_plan_changed(session, membership.trip_id)
    previous_id = None if latest is None else latest.id
    session.add(row)
    if computed.alternative is not None:
        await session.flush()
        strict = PlanVersion(
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
        session.add(strict)
        await session.flush()
        await approval_service.open_for_plan(session, row, strict)
    await session.commit()
    await session.refresh(row)
    if queue is not None and not draft:
        await _start_justifications(
            session, queue, membership.sub, row.id, locale, previous_id
        )
    return await _read(session, queue, membership, row, locale, latest=True), True


def computation_progress(trip_id: UUID) -> PlanProgressRead | None:
    """The stage of the trip's plan computation that is running now.

    Args:
        trip_id: Trip id (the caller's membership is already checked).

    Returns:
        The stage, or None when no plan is being computed for the trip.
    """
    event = plan_progress.current(trip_id)
    if event is None:
        return None
    return PlanProgressRead(
        step=event.step,
        position=event.position,
        total=len(PLAN_STEPS),
        item=event.item,
        items=event.items,
    )


async def latest_plan(
    session: AsyncSession,
    membership: TripMembership,
    locale: Locale = "pl",
    *,
    queue: JobQueue | None = None,
) -> PlanRead:
    """Newest version of the trip's plan.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        locale: Language of the verdict justifications.
        queue: Job queue (model justifications); None gives templates.

    Returns:
        The plan.

    Raises:
        PlanNotFoundError: When the trip has no plan yet.
    """
    row = await db.select_latest(session, membership.trip_id)
    if row is None:
        raise PlanNotFoundError(str(membership.trip_id))
    return await _read(session, queue, membership, row, locale, latest=True)


async def get_plan(
    session: AsyncSession,
    membership: TripMembership,
    plan_id: UUID,
    locale: Locale = "pl",
    *,
    queue: JobQueue | None = None,
) -> PlanRead:
    """One stored version.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        plan_id: Version id.
        locale: Language of the verdict justifications.
        queue: Job queue (model justifications); None gives templates.

    Returns:
        The plan.

    Raises:
        PlanNotFoundError: When the trip has no such version.
    """
    row = await db.select_by_id(session, membership.trip_id, plan_id)
    if row is None:
        raise PlanNotFoundError(str(plan_id))
    newest = await db.select_latest(session, membership.trip_id)
    is_latest = newest is not None and newest.id == row.id
    return await _read(session, queue, membership, row, locale, latest=is_latest)


IMPACT_CACHE_SIZE = 32
"""Measurements kept in this process (the same trip data asks again within a turn)."""

impact_cache: dict[tuple[str, tuple[WhatIfTarget, ...]], dict[WhatIfTarget, float]] = {}


async def measure_impacts(
    session: AsyncSession,
    membership: TripMembership,
    assumptions: PlanAssumptions | None,
    targets: tuple[WhatIfTarget, ...],
    budget_seconds: float,
) -> dict[WhatIfTarget, float] | None:
    """How much would each answer change the plan for what is known now?

    Solves the trip's plan (with the assumptions of a draft, if given) and once
    more for each plausible answer of each question, outside the event loop.
    Nothing is stored. The same data and questions give the same scores, and a
    repeat within the process is served from a small cache.

    Args:
        session: Open session.
        membership: The caller's membership (any role; the data is read host-level).
        assumptions: Gaps to fill in memory, as for a draft plan.
        targets: The questions to measure.
        budget_seconds: Give up after this long.

    Returns:
        The score of each question, or None when the time ran out.

    Raises:
        PlanInputError: The trip cannot be planned (no city, unknown city).
    """
    planning, _names, alpha = await gather_input(session, membership, assumptions)
    version, params = await parameters_service.current(session)
    await session.rollback()  # do not hold a transaction while computing
    key = (
        input_hash(planning, alpha, "impact", params, parameters_version=version),
        targets,
    )
    if (cached := impact_cache.get(key)) is not None:
        return cached
    scores = await anyio.to_thread.run_sync(
        partial(
            what_if.impacts,
            planning,
            targets,
            params,
            alpha=alpha,
            budget_seconds=budget_seconds,
        )
    )
    if scores is not None:
        if len(impact_cache) >= IMPACT_CACHE_SIZE:
            impact_cache.pop(next(iter(impact_cache)))
        impact_cache[key] = scores
    return scores


class PlanNotApprovedError(Exception):
    """The plan version has no approved proposal, so it cannot be exported."""


@dataclass(frozen=True, slots=True)
class CalendarFile:
    """An ``.ics`` file and the name to save it under."""

    content: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class ApprovedPlan:
    """An approved plan version with what an export needs from its trip."""

    plan: PlanRead
    trip_name: str
    timezone: str


async def approved_plan(
    session: AsyncSession, membership: TripMembership, plan_id: UUID
) -> ApprovedPlan:
    """A plan version that every member with an account approved.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        plan_id: The version.

    Returns:
        The version, the trip name and the time zone of the city.

    Raises:
        PlanNotFoundError: When the trip has no such version.
        PlanNotApprovedError: When the version has no approved proposal.
    """
    row = await db.select_by_id(session, membership.trip_id, plan_id)
    if row is None:
        raise PlanNotFoundError(str(plan_id))
    if await proposal_service.approved_at(session, membership, plan_id) is None:
        raise PlanNotApprovedError(str(plan_id))
    host_view = membership.model_copy(update={"role": TripRole.HOST})
    trip = await trip_service.get_trip(session, host_view)
    cities = {c.slug: c for c in await place_service.list_cities(session)}
    city = cities[trip.city_slug or ""]
    return ApprovedPlan(plan=_stored(row), trip_name=trip.name, timezone=city.timezone)


async def export_calendar(
    session: AsyncSession, membership: TripMembership, plan_id: UUID
) -> CalendarFile:
    """The approved plan version as an iCalendar file.

    Only a version that every member with an account approved can be exported;
    the file is the same for the same version, byte for byte.

    Args:
        session: Open session.
        membership: Proof that the caller may use the trip.
        plan_id: The version.

    Returns:
        The file and its name.

    Raises:
        PlanNotFoundError: When the trip has no such version.
        PlanNotApprovedError: When the version has no approved proposal.
    """
    approved = await approved_plan(session, membership, plan_id)
    plan = approved.plan
    return CalendarFile(
        content=build_ics(
            plan, approved.timezone, f"{CALENDAR_PREFIX} {approved.trip_name}"
        ),
        filename=f"{FILE_PREFIX}-v{plan.version}-{plan.plan_hash}.ics",
    )


async def latest_place_ids(session: AsyncSession, trip_id: UUID) -> list[UUID]:
    """Places of the trip's newest plan, in visiting order, without repeats.

    For callers that already checked access (the voting link), and read no plan
    content other than which places it has.

    Args:
        session: Open session.
        trip_id: Trip id.

    Returns:
        Place ids; empty when the trip has no plan yet.
    """
    row = await db.select_latest(session, trip_id)
    if row is None:
        return []
    found = (
        UUID(item["place_id"]) for day in row.result["days"] for item in day["items"]
    )
    return list(dict.fromkeys(found))
