"""Check a pasted lodging offer: quotes from the worker, three states from code."""

from datetime import date, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.accommodation import db
from tuttitrip.accommodation.logic.contract import (
    OfferFacts,
    check_offer,
    lodging_score,
)
from tuttitrip.accommodation.logic.keys import RequirementKind
from tuttitrip.accommodation.logic.platforms import link_host, platform_of
from tuttitrip.accommodation.models import AccommodationOffer
from tuttitrip.accommodation.schemas import (
    OfferCheckState,
    OfferCreate,
    OfferFeatures,
    OfferRead,
    Requirement,
    RequirementsRead,
    requirement_label,
)
from tuttitrip.accommodation.services import requirements_service
from tuttitrip.planning.linter.schemas import DocumentKind
from tuttitrip.planning.linter.services import document_service
from tuttitrip.shared.jobs.contracts import (
    EvidenceQuote,
    ExtractOfferEvidenceInput,
    ExtractOfferEvidenceOutput,
    RequirementEvidence,
    RequirementLabel,
    Workflow,
    queue_for,
)
from tuttitrip.shared.jobs.services.job_queue import JobNotFoundError, JobQueue
from tuttitrip.shared.jobs.services.worker_liveness import ensure_worker_available
from tuttitrip.trips.schemas import TripMembership, TripRead
from tuttitrip.trips.services import trip_service

_RUNNING = frozenset({"ENQUEUED", "DELAYED", "PENDING"})


class OfferInvalidError(Exception):
    """The offer does not fit the trip (no such text, nights outside the trip)."""


class OfferNotFoundError(Exception):
    """The trip has no offer with this id."""


def _check_nights(trip: TripRead, nights: list[date]) -> None:
    if trip.kind == "outing":
        msg = "An outing has no overnight stays, so it takes no lodging offers"
        raise OfferInvalidError(msg)
    if trip.start_date is None or trip.end_date is None:
        return
    last_night = trip.end_date - timedelta(days=1)
    if nights[0] < trip.start_date or nights[-1] > last_night:
        msg = (
            f"Nights must be between {trip.start_date} and {last_night} "
            "(the night of the last day is not part of the trip)"
        )
        raise OfferInvalidError(msg)


def _model_keys(current: RequirementsRead, features: OfferFeatures) -> list[str]:
    answered = features.present | features.absent
    return [
        item.key
        for item in current.requirements
        if item.kind is RequirementKind.AMENITY and item.key not in answered
    ]


async def create_offer(
    session: AsyncSession,
    queue: JobQueue,
    membership: TripMembership,
    data: OfferCreate,
) -> OfferRead:
    """Store an offer and, when an amenity needs quotes, start the worker job.

    Platform requirements are decided by the link's domain and distance ones
    stay unconfirmed, so neither goes to the worker; amenities the host answered
    in ``features`` do not either. Without such keys no job is started. The
    job is keyed by the new offer, so pasting again retries a failed check.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (co-host or host).
        data: The offer.

    Returns:
        The stored offer with its current check.

    Raises:
        OfferInvalidError: Outing, nights outside the trip, or no pasted offer
            text with this id on the trip.
    """
    trip = await trip_service.get_trip(session, membership)
    _check_nights(trip, data.nights)
    document = await document_service.get_document(
        session, membership.trip_id, data.document_id
    )
    if document is None or document.kind is not DocumentKind.OFFER:
        msg = "No pasted offer with this document_id on the trip"
        raise OfferInvalidError(msg)
    current = await requirements_service.get_requirements(session, membership.trip_id)
    keys = _model_keys(current, data.features)
    offer_id = uuid4()
    job_id = None
    if keys:
        await ensure_worker_available(session)
        payload = ExtractOfferEvidenceInput(
            trip_id=membership.trip_id,
            document_id=data.document_id,
            requirement_keys=keys,
            requirements=[
                RequirementLabel(key=key, label=requirement_label(key)) for key in keys
            ],
            provider=data.provider,
        )
        job_id = await queue.enqueue(
            Workflow.EXTRACT_OFFER_EVIDENCE,
            payload,
            user=membership.sub,
            key=str(offer_id),
            queue=queue_for(data.provider),
        )
    row = AccommodationOffer(
        id=offer_id,
        trip_id=membership.trip_id,
        document_id=data.document_id,
        nights=data.nights,
        url=data.url,
        platform=platform_of(link_host(data.url)),
        features=data.features.model_dump(mode="json"),
        requirements_version=current.version,
        requested_keys=keys,
        job_id=job_id,
        evidence=None,
        job_failed=False,
        error_code=None,
        created_by=membership.sub,
    )
    await db.insert_offer(session, row)
    await session.commit()
    return _read(row, current, pending=job_id is not None)


async def get_offer(
    session: AsyncSession, queue: JobQueue, membership: TripMembership, offer_id: UUID
) -> OfferRead:
    """Read an offer and its check against the trip's current requirements.

    While the worker job runs the quoted requirements are ``pending``. The first
    read after it succeeds stores its quotes on the offer, so later reads do not
    depend on the job system.

    Args:
        session: Open session.
        queue: Job queue.
        membership: Proof from ``TripAccess`` (any member).
        offer_id: Offer id.

    Returns:
        The offer with its checks and ``S_h``.

    Raises:
        OfferNotFoundError: The trip has no such offer.
    """
    row = await db.select_offer(session, membership.trip_id, offer_id)
    if row is None:
        raise OfferNotFoundError(str(offer_id))
    pending = False
    if row.job_id is not None and row.evidence is None and not row.job_failed:
        state, error_code, evidence = await _job_result(queue, row.job_id)
        if state is OfferCheckState.PENDING:
            pending = True
        else:
            if evidence is not None:
                row.evidence = [item.model_dump(mode="json") for item in evidence]
            else:
                row.job_failed, row.error_code = True, error_code
            await session.commit()
    current = await requirements_service.get_requirements(session, membership.trip_id)
    return _read(row, current, pending=pending)


async def _job_result(
    queue: JobQueue, job_id: str
) -> tuple[OfferCheckState, str | None, list[RequirementEvidence] | None]:
    try:
        job = await queue.get(job_id)
    except JobNotFoundError:
        return OfferCheckState.FAILED, None, None
    if job.status in _RUNNING:
        return OfferCheckState.PENDING, None, None
    if job.status != "SUCCESS":
        return OfferCheckState.FAILED, job.error_code, None
    try:
        output = ExtractOfferEvidenceOutput.model_validate(job.output or {})
    except ValidationError:
        return OfferCheckState.FAILED, None, None
    return OfferCheckState.DONE, None, output.evidence


def _read(
    row: AccommodationOffer,
    current: RequirementsRead,
    *,
    pending: bool,
) -> OfferRead:
    if pending:
        state = OfferCheckState.PENDING
    elif row.job_failed:
        state = OfferCheckState.FAILED
    else:
        state = OfferCheckState.DONE
    evidence: dict[str, list[EvidenceQuote]] | None = None
    if row.evidence is not None:
        items = [RequirementEvidence.model_validate(e) for e in row.evidence]
        evidence = {item.requirement_key: list(item.quotes) for item in items}
    facts = OfferFacts(
        host=link_host(row.url),
        features=OfferFeatures.model_validate(row.features),
        requested=frozenset(row.requested_keys),
        evidence=evidence,
        failed=state is OfferCheckState.FAILED,
    )
    requirements = [
        Requirement(feature=item.key, kind=item.kind, hard=item.hard)
        for item in current.requirements
    ]
    checks = check_offer(requirements, facts)
    return OfferRead(
        id=row.id,
        trip_id=row.trip_id,
        document_id=row.document_id,
        nights=sorted(row.nights),
        url=row.url,
        platform=row.platform,
        state=state,
        job_id=row.job_id,
        error_code=row.error_code,
        requirements_version=row.requirements_version,
        stale=row.requirements_version != current.version,
        checks=checks,
        score=lodging_score(checks),
        created_at=row.created_at,
    )
