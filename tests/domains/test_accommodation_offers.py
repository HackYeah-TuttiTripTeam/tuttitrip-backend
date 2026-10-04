"""Pasted offer check: three states with a quote, S_h, endpoints and the lint rule."""

import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import EVERYTHING, FakeJobQueue, authorize
from tests.shared.paths import path
from tuttitrip.accommodation import db
from tuttitrip.accommodation.logic.contract import (
    MIN_CONFIDENCE,
    RHO_UNC,
    OfferFacts,
    check_offer,
    evaluate_evidence,
    lodging_score,
)
from tuttitrip.accommodation.logic.keys import KEYS, Platform, RequirementKind
from tuttitrip.accommodation.logic.platforms import link_host, platform_of
from tuttitrip.accommodation.models import AccommodationOffer
from tuttitrip.accommodation.schemas import (
    LABELS,
    OfferFeatures,
    Requirement,
    RequirementCheck,
    RequirementItem,
    RequirementsRead,
    RequirementStatus,
    UnconfirmedReason,
    requirement_label,
)
from tuttitrip.accommodation.services import offer_service, requirements_service
from tuttitrip.main import create_app
from tuttitrip.planning.linter.logic import accommodation_requirements
from tuttitrip.planning.linter.schemas import (
    DocumentKind,
    DocumentRead,
    LintContext,
    LintLodging,
    LintOffer,
    LintPlan,
    Severity,
)
from tuttitrip.planning.linter.services import document_service
from tuttitrip.planning.logic import domains
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.jobs.api import get_job_queue
from tuttitrip.shared.jobs.contracts import (
    EvidenceQuote,
    ExtractOfferEvidenceInput,
    Queue,
    RequirementLabel,
)
from tuttitrip.shared.jobs.services.job_queue import JobQueueUnavailableError
from tuttitrip.shared.jobs.services.worker_liveness import WorkerUnavailableError
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import MemberStatus, TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

POOL = Requirement(feature="pool")
NO_POOL = "Na terenie obiektu nie ma basenu."
HAS_POOL = "Do dyspozycji gości basen odkryty."


def quote(text: str, verdict: str | None, confidence: float | None) -> EvidenceQuote:
    return EvidenceQuote.model_validate(
        {"text": text, "verdict": verdict, "confidence": confidence}
    )


# --- contract logic -------------------------------------------------------------


def test_silent_offer_is_unconfirmed_with_no_mention() -> None:
    check = evaluate_evidence(POOL, [])
    assert check.status is RequirementStatus.UNCONFIRMED
    assert check.reason is UnconfirmedReason.NO_MENTION
    assert check.quote is None


def test_confident_absence_is_unmet_with_the_quote() -> None:
    check = evaluate_evidence(POOL, [quote(NO_POOL, "absent", 0.9)])
    assert check.status is RequirementStatus.UNMET
    assert check.quote == NO_POOL
    assert check.confidence == pytest.approx(0.9)
    assert check.reason is None


def test_confident_presence_is_met_with_the_best_quote() -> None:
    quotes = [quote("basen", "present", 0.5), quote(HAS_POOL, "present", 0.8)]
    check = evaluate_evidence(POOL, quotes)
    assert check.status is RequirementStatus.MET
    assert check.quote == HAS_POOL


@pytest.mark.parametrize(
    ("quotes", "reason"),
    [
        ([quote(HAS_POOL, "present", 0.1)], UnconfirmedReason.LOW_CONFIDENCE),
        ([quote(HAS_POOL, "present", None)], UnconfirmedReason.LOW_CONFIDENCE),
        ([quote(HAS_POOL, None, None)], UnconfirmedReason.NOT_ASSESSED),
        ([quote("Parking", "not_applicable", 0.9)], UnconfirmedReason.NO_MENTION),
        (
            [quote(HAS_POOL, "present", 0.9), quote(NO_POOL, "absent", 0.9)],
            UnconfirmedReason.CONFLICTING,
        ),
    ],
)
def test_everything_else_is_unconfirmed(
    quotes: list[EvidenceQuote], reason: UnconfirmedReason
) -> None:
    check = evaluate_evidence(POOL, quotes)
    assert check.status is RequirementStatus.UNCONFIRMED
    assert check.reason is reason


def test_conflict_keeps_a_quote_to_show() -> None:
    quotes = [quote(HAS_POOL, "present", 0.6), quote(NO_POOL, "absent", 0.9)]
    check = evaluate_evidence(POOL, quotes)
    assert check.reason is UnconfirmedReason.CONFLICTING
    assert check.quote == NO_POOL
    assert check.confidence is not None
    assert check.confidence >= MIN_CONFIDENCE


def test_every_requirement_key_has_a_label() -> None:
    keys = {member.value for enum in KEYS.values() for member in enum}
    assert set(LABELS) == keys
    assert requirement_label("family_room") == "family room"
    assert requirement_label("sauna_room") == "sauna room"


def test_threshold_is_a_parameter() -> None:
    quotes = [quote(HAS_POOL, "present", 0.2)]
    assert evaluate_evidence(POOL, quotes, min_confidence=0.1).status is (
        RequirementStatus.MET
    )
    assert evaluate_evidence(POOL, quotes, min_confidence=0.3).status is (
        RequirementStatus.UNCONFIRMED
    )


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.airbnb.pl/rooms/123", Platform.AIRBNB),
        ("https://airbnb.com/rooms/1", Platform.AIRBNB),
        ("https://www.airbnb.co.uk/rooms/1", Platform.AIRBNB),
        ("https://abnb.me/xyz", Platform.AIRBNB),
        ("https://www.booking.com/hotel/pl/x.html", Platform.BOOKING),
        ("https://m.booking.com/hotel", Platform.BOOKING),
        ("https://notbooking.com/", None),
        ("https://booking.com.evil.io/", None),
        ("https://example.com/airbnb", None),
    ],
)
def test_platform_from_the_link_domain(url: str, platform: Platform | None) -> None:
    assert platform_of(link_host(url)) is platform


AIRBNB_ONLY = Requirement(feature="airbnb", kind=RequirementKind.PLATFORM)


def test_only_airbnb_with_a_booking_link_is_unmet_quoting_the_domain() -> None:
    facts = OfferFacts(host=link_host("https://www.booking.com/hotel/pl/x.html"))
    [check] = check_offer([AIRBNB_ONLY], facts)
    assert check.status is RequirementStatus.UNMET
    assert check.quote == "www.booking.com"


def test_platform_without_a_link_is_unconfirmed() -> None:
    [check] = check_offer([AIRBNB_ONLY], OfferFacts())
    assert check.reason is UnconfirmedReason.NO_LINK


def test_hard_platforms_form_an_allowed_set() -> None:
    booking = Requirement(feature="booking", kind=RequirementKind.PLATFORM)
    checks = check_offer([AIRBNB_ONLY, booking], OfferFacts(host="booking.com"))
    assert {c.status for c in checks} == {RequirementStatus.MET}


def test_amenity_sources_and_states() -> None:
    wifi, parking, crib = (Requirement(feature=k) for k in ("wifi", "parking", "crib"))
    distance = Requirement(
        feature="attractions", kind=RequirementKind.DISTANCE, hard=False
    )
    facts = OfferFacts(
        features=OfferFeatures(present={"wifi"}),
        requested=frozenset({"pool", "parking"}),
        evidence={"pool": [quote(NO_POOL, "absent", 0.9)]},
    )
    checks = check_offer([wifi, POOL, parking, crib, distance], facts)
    assert [(c.status, c.reason) for c in checks] == [
        (RequirementStatus.MET, None),
        (RequirementStatus.UNMET, None),
        (RequirementStatus.UNCONFIRMED, UnconfirmedReason.NO_MENTION),
        (RequirementStatus.UNCONFIRMED, UnconfirmedReason.NOT_CHECKED),
        (RequirementStatus.UNCONFIRMED, UnconfirmedReason.NOT_CHECKED),
    ]
    pending = OfferFacts(requested=frozenset({"pool"}))
    assert check_offer([POOL], pending)[0].reason is UnconfirmedReason.PENDING
    failed = OfferFacts(requested=frozenset({"pool"}), failed=True)
    assert check_offer([POOL], failed)[0].reason is UnconfirmedReason.CHECK_FAILED


def _checked(status: RequirementStatus, *, hard: bool) -> RequirementCheck:
    reason = UnconfirmedReason.NO_MENTION if status == "unconfirmed" else None
    return RequirementCheck(feature="x", hard=hard, status=status, reason=reason)


def test_lodging_score_is_product_of_hard_times_mean_of_soft() -> None:
    assert lodging_score([]) == 1
    hard_unc = _checked(RequirementStatus.UNCONFIRMED, hard=True)
    hard_met = _checked(RequirementStatus.MET, hard=True)
    soft_met = _checked(RequirementStatus.MET, hard=False)
    soft_unmet = _checked(RequirementStatus.UNMET, hard=False)
    score = lodging_score([hard_unc, hard_met, soft_met, soft_unmet])
    assert score == pytest.approx(RHO_UNC * 0.5)
    assert lodging_score([_checked(RequirementStatus.UNMET, hard=True)]) == 0


def test_unconfirmed_points_match_the_planning_parameter() -> None:
    assert DEFAULT_PARAMS.uncertain_requirement == RHO_UNC


@pytest.mark.parametrize(
    "statuses",
    [
        [],
        [("met", True), ("unconfirmed", True), ("met", False), ("unmet", False)],
        [("unconfirmed", True), ("unconfirmed", False)],
        [("unmet", True), ("met", False)],
        [("met", False), ("unconfirmed", False), ("unconfirmed", False)],
    ],
)
def test_offer_score_equals_the_planning_lodging_domain(
    statuses: list[tuple[str, bool]],
) -> None:
    checks = [
        _checked(RequirementStatus(status), hard=hard) for status, hard in statuses
    ]
    outcomes = [
        domains.RequirementOutcome(hard=hard, status=RequirementStatus(status))
        for status, hard in statuses
    ]
    assert 100 * lodging_score(checks) == pytest.approx(domains.lodging_score(outcomes))


# --- lint rule ------------------------------------------------------------------

N1, N2 = date(2026, 7, 1), date(2026, 7, 2)
HARD_POOL = RequirementItem(kind=RequirementKind.AMENITY, key="pool", hard=True)


def lint(offers: list[LintOffer]) -> list[Any]:
    lodging = LintLodging(nights=[N1, N2], requirements=[HARD_POOL], offers=offers)
    context = LintContext(
        places=[], timezone="Europe/Warsaw", budget=0, lodging=lodging
    )
    return accommodation_requirements.check(LintPlan(days=[]), context)


def test_night_without_offer_is_a_warning_and_unmet_is_a_violation() -> None:
    unmet = RequirementCheck(
        feature="pool", status=RequirementStatus.UNMET, quote=NO_POOL
    )
    findings = lint([LintOffer(nights=[N1], checks=[unmet])])
    assert [(f.day, f.severity) for f in findings] == [
        (N1, Severity.VIOLATION),
        (N2, Severity.WARNING),
    ]
    assert NO_POOL in findings[0].message
    assert findings[1].message.endswith("no offer for this night")


def test_messages_use_labels_not_keys() -> None:
    family = RequirementItem(kind=RequirementKind.AMENITY, key="family_room", hard=True)
    lodging = LintLodging(nights=[N1], requirements=[family], offers=[])
    context = LintContext(
        places=[], timezone="Europe/Warsaw", budget=0, lodging=lodging
    )
    [finding] = accommodation_requirements.check(LintPlan(days=[]), context)
    assert "family room" in finding.message
    assert "family_room" not in finding.message


def test_every_reason_has_a_readable_text() -> None:
    assert set(accommodation_requirements.REASONS) == set(UnconfirmedReason)


def test_best_offer_of_the_night_wins_and_no_lodging_means_no_findings() -> None:
    unmet = RequirementCheck(feature="pool", status=RequirementStatus.UNMET)
    met = RequirementCheck(feature="pool", status=RequirementStatus.MET)
    offers = [
        LintOffer(nights=[N1, N2], checks=[unmet]),
        LintOffer(nights=[N1, N2], checks=[met]),
    ]
    assert lint(offers) == []
    context = LintContext(places=[], timezone="Europe/Warsaw", budget=0)
    assert accommodation_requirements.check(LintPlan(days=[]), context) == []


# --- endpoints ------------------------------------------------------------------

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()
DOC = uuid.uuid4()
CREATED = datetime(2026, 10, 4, tzinfo=UTC)


def _trip() -> TripRead:
    return TripRead.model_validate(
        {
            "id": TRIP,
            "name": "Gdańsk",
            "destination": None,
            "created_at": CREATED,
            "start_date": N1,
            "end_date": date(2026, 7, 4),
            "day_start": "09:00",
            "day_end": "19:00",
            "city_slug": None,
            "currency": None,
            "budget_total_min": None,
            "budget_total_max": None,
            "budget_day_min": None,
            "budget_day_max": None,
            "budget_flex_pct": 10,
            "fairness_alpha": 1.0,
            "my_role": TripRole.CO_HOST,
            "my_status": MemberStatus.CONFIRMED,
        }
    )


class _Session:
    commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Offers:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, AccommodationOffer] = {}

    async def insert_offer(self, _s: object, row: AccommodationOffer) -> None:
        row.created_at = CREATED
        self.rows[row.id] = row

    async def select_offer(
        self, _s: object, trip_id: uuid.UUID, offer_id: uuid.UUID
    ) -> AccommodationOffer | None:
        row = self.rows.get(offer_id)
        return row if row is not None and row.trip_id == trip_id else None


class Env:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.queue = FakeJobQueue()
        self.offers = _Offers()
        self.session = _Session()
        self.role = TripRole.CO_HOST
        self.requirements = RequirementsRead(
            requirements=[
                HARD_POOL,
                RequirementItem(kind=RequirementKind.PLATFORM, key="airbnb", hard=True),
            ],
            version=3,
        )
        self.document = DocumentRead(
            id=DOC,
            trip_id=TRIP,
            kind=DocumentKind.OFFER,
            created_by=BOB.sub,
            created_at=CREATED,
        )
        self.worker = AsyncMock()

        def membership(
            _s: object, _t: uuid.UUID, _sub: str, min_role: TripRole
        ) -> TripMembership:
            if not self.role.satisfies(min_role):
                msg = f"Trip role '{min_role}' required"
                raise trip_service.TripRoleError(msg)
            return TripMembership(trip_id=TRIP, sub=BOB.sub, role=self.role)

        def requirements(_s: object, _t: uuid.UUID) -> RequirementsRead:
            return self.requirements

        def document(
            _s: object, trip_id: uuid.UUID, document_id: uuid.UUID
        ) -> DocumentRead | None:
            ok = trip_id == TRIP and document_id == self.document.id
            return self.document if ok else None

        patch = monkeypatch.setattr
        patch(trip_service, "get_membership", AsyncMock(side_effect=membership))
        patch(trip_service, "get_trip", AsyncMock(return_value=_trip()))
        patch(
            requirements_service,
            "get_requirements",
            AsyncMock(side_effect=requirements),
        )
        patch(document_service, "get_document", AsyncMock(side_effect=document))
        patch(db, "insert_offer", self.offers.insert_offer)
        patch(db, "select_offer", self.offers.select_offer)
        patch(offer_service, "ensure_worker_available", self.worker)

    def client(
        self, *, auth: bool = True, grants: tuple[Grant, ...] = EVERYTHING
    ) -> TestClient:
        app = create_app()
        if auth:
            authorize(app, BOB, grants)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_job_queue] = lambda: self.queue
        return TestClient(app)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(monkeypatch)


BODY: dict[str, Any] = {
    "document_id": str(DOC),
    "nights": ["2026-07-02", "2026-07-01"],
    "url": "https://www.booking.com/hotel/pl/morze.html",
}


def _status(body: dict[str, Any], feature: str) -> tuple[object, object]:
    check = next(c for c in body["checks"] if c["feature"] == feature)
    return check["status"], check["reason"]


def test_post_enqueues_only_amenities_and_decides_the_platform_now(env: Env) -> None:
    with env.client() as client:
        response = client.post(path("create_offer", trip_id=TRIP), json=BODY)
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "pending"
    assert body["platform"] == "booking"
    assert body["nights"] == ["2026-07-01", "2026-07-02"]
    assert _status(body, "airbnb") == ("unmet", None)
    assert _status(body, "pool") == ("unconfirmed", "pending")
    assert body["score"] == 0
    [payload] = env.queue.payloads.values()
    assert isinstance(payload, ExtractOfferEvidenceInput)
    assert payload.requirement_keys == ["pool"]
    assert payload.requirements == [RequirementLabel(key="pool", label="pool")]
    assert env.queue.queues[body["job_id"]] is Queue.OPENROUTER
    env.worker.assert_awaited_once()
    assert env.session.commits == 1


def test_post_without_amenities_to_quote_starts_no_job(env: Env) -> None:
    env.requirements = RequirementsRead(
        requirements=[env.requirements.requirements[1]], version=1
    )
    with env.client() as client:
        response = client.post(path("create_offer", trip_id=TRIP), json=BODY)
    assert response.status_code == 202
    assert response.json()["job_id"] is None
    assert response.json()["state"] == "done"
    assert env.queue.payloads == {}
    env.worker.assert_not_awaited()


def test_host_answers_skip_the_model(env: Env) -> None:
    body = {**BODY, "features": {"present": ["pool"], "absent": []}}
    with env.client() as client:
        response = client.post(path("create_offer", trip_id=TRIP), json=body)
    assert response.json()["job_id"] is None
    assert _status(response.json(), "pool") == ("met", None)


def test_get_reads_the_job_stores_evidence_and_marks_stale(env: Env) -> None:
    with env.client() as client:
        created = client.post(path("create_offer", trip_id=TRIP), json=BODY).json()
        job = env.queue.jobs[created["job_id"]]
        output = {
            "contract_version": 1,
            "evidence": [
                {
                    "requirement_key": "pool",
                    "quotes": [
                        {"text": NO_POOL, "verdict": "absent", "confidence": 0.9}
                    ],
                }
            ],
        }
        env.queue.jobs[job.workflow_id] = job.model_copy(
            update={"status": "SUCCESS", "output": output}
        )
        url = path("get_offer", trip_id=TRIP, offer_id=created["id"])
        body = client.get(url).json()
        assert body["state"] == "done"
        check = next(c for c in body["checks"] if c["feature"] == "pool")
        assert (check["status"], check["quote"]) == ("unmet", NO_POOL)
        assert env.offers.rows[uuid.UUID(created["id"])].evidence is not None
        assert body["stale"] is False
        env.requirements = env.requirements.model_copy(
            update={
                "version": 4,
                "requirements": [
                    *env.requirements.requirements,
                    RequirementItem(
                        kind=RequirementKind.AMENITY, key="parking", hard=False
                    ),
                ],
            }
        )
        env.queue.jobs.clear()  # stored evidence no longer needs the queue
        again = client.get(url).json()
    assert again["stale"] is True
    assert _status(again, "parking") == ("unconfirmed", "not_checked")


def test_failed_job_makes_quoted_requirements_unconfirmed(env: Env) -> None:
    with env.client() as client:
        created = client.post(path("create_offer", trip_id=TRIP), json=BODY).json()
        job = env.queue.jobs[created["job_id"]]
        env.queue.jobs[job.workflow_id] = job.model_copy(
            update={"status": "ERROR", "error_code": "document_not_found"}
        )
        url = path("get_offer", trip_id=TRIP, offer_id=created["id"])
        body = client.get(url).json()
        row = env.offers.rows[uuid.UUID(created["id"])]
        assert (row.job_failed, row.error_code) == (True, "document_not_found")
        env.queue.jobs.clear()  # settled: the next read does not poll the queue
        again = client.get(url).json()
    assert (body["state"], body["error_code"]) == ("failed", "document_not_found")
    assert again == body
    assert _status(body, "pool") == ("unconfirmed", "check_failed")


@pytest.mark.parametrize("failure", ["worker", "queue"])
def test_unavailable_worker_or_queue_is_503_and_stores_nothing(
    env: Env, failure: str
) -> None:
    if failure == "worker":
        env.worker.side_effect = WorkerUnavailableError("No worker")
    else:
        env.monkeypatch.setattr(
            env.queue,
            "enqueue",
            AsyncMock(side_effect=JobQueueUnavailableError("down")),
        )
    with env.client() as client:
        response = client.post(path("create_offer", trip_id=TRIP), json=BODY)
    assert response.status_code == 503
    assert env.offers.rows == {}
    assert env.session.commits == 0


@pytest.mark.parametrize(
    "change",
    [
        {"nights": ["2026-07-03", "2026-07-03"]},
        {"nights": ["2026-07-04"]},
        {"nights": []},
        {"document_id": str(uuid.uuid4())},
        {"url": "javascript:alert(1)"},
    ],
)
def test_bad_offer_is_422(env: Env, change: dict[str, Any]) -> None:
    with env.client() as client:
        response = client.post(
            path("create_offer", trip_id=TRIP), json={**BODY, **change}
        )
    assert response.status_code == 422


def test_plan_text_is_not_an_offer(env: Env) -> None:
    env.document = env.document.model_copy(update={"kind": DocumentKind.PLAN})
    with env.client() as client:
        response = client.post(path("create_offer", trip_id=TRIP), json=BODY)
    assert response.status_code == 422


def test_access_401_403_404(env: Env) -> None:
    with env.client(auth=False) as client:
        assert (
            client.post(path("create_offer", trip_id=TRIP), json=BODY).status_code
            == 401
        )
    read_only = (Grant(Feature.ACCOMMODATION, Access.READ),)
    with env.client(grants=read_only) as client:
        post = client.post(path("create_offer", trip_id=TRIP), json=BODY)
        assert post.status_code == 403
        assert post.json()["detail"] == "Missing permission accommodation:WRITE"
    env.role = TripRole.MEMBER
    with env.client() as client:
        post = client.post(path("create_offer", trip_id=TRIP), json=BODY)
        assert post.status_code == 403
        missing = path("get_offer", trip_id=TRIP, offer_id=uuid.uuid4())
        assert client.get(missing).status_code == 404
    env.monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(side_effect=TripNotFoundError(str(TRIP))),
    )
    with env.client() as client:
        assert (
            client.post(path("create_offer", trip_id=TRIP), json=BODY).status_code
            == 404
        )
