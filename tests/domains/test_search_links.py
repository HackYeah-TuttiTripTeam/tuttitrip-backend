"""Lodging search links: pure URL building, the service rules and the endpoints."""

import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.accommodation import db
from tuttitrip.accommodation.logic.keys import Platform, RequirementKind
from tuttitrip.accommodation.logic.search_links import (
    SearchInput,
    build_links,
    nightly_cap,
)
from tuttitrip.accommodation.models import SearchOpening
from tuttitrip.accommodation.schemas import (
    RequirementItem,
    RequirementsRead,
    SearchLinksRead,
)
from tuttitrip.accommodation.services import requirements_service, search_links_service
from tuttitrip.main import create_app
from tuttitrip.places.schemas import CityRead
from tuttitrip.places.services import place_service
from tuttitrip.profiles.schemas import ProfileRead
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import MemberStatus, TripMembership, TripRead, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.uuid4()
BOTH = frozenset(Platform)
FAMILY = SearchInput(
    check_in=date(2026, 11, 6),
    check_out=date(2026, 11, 9),
    ages=(40, 38, 6, 13),
    area="Kraków",
    max_price_per_night=600,
    currency="PLN",
)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_family_links_carry_dates_group_and_price() -> None:
    booking, airbnb = build_links(FAMILY, BOTH)
    q = _query(booking.url)
    assert booking.platform is Platform.BOOKING
    assert urlsplit(booking.url).netloc == "www.booking.com"
    assert q["checkin"] == ["2026-11-06"]
    assert q["checkout"] == ["2026-11-09"]
    assert q["group_adults"] == ["2"]
    assert q["group_children"] == ["2"]
    assert q["age"] == ["13", "6"]
    assert q["ss"] == ["Kraków"]
    assert q["nflt"] == ["price=PLN-min-600-1"]
    a = _query(airbnb.url)
    assert urlsplit(airbnb.url).netloc == "www.airbnb.com"
    assert urlsplit(airbnb.url).path == "/s/Krak%C3%B3w/homes"
    assert (a["adults"], a["children"], a["price_max"]) == (["2"], ["2"], ["600"])
    assert a["currency"] == ["PLN"]
    assert "infants" not in a


def test_unofficial_parameters_are_flagged() -> None:
    booking, airbnb = build_links(FAMILY, BOTH)
    official = {p.name for p in booking.params if p.official}
    assert official == {"checkin", "checkout", "group_adults", "no_rooms"}
    assert not any(p.official for p in airbnb.params)


def test_airbnb_splits_infants_from_children() -> None:
    search = SearchInput(date(2026, 11, 6), date(2026, 11, 7), (30, 1, 5))
    (airbnb,) = build_links(search, frozenset({Platform.AIRBNB}))
    q = _query(airbnb.url)
    assert (q["children"], q["infants"]) == (["1"], ["1"])


def test_values_are_percent_encoded_and_hosts_fixed() -> None:
    evil = SearchInput(
        date(2026, 11, 6),
        date(2026, 11, 7),
        (30,),
        area="x&group_adults=9#/@evil.com ?a=b",
    )
    for link in build_links(evil, BOTH):
        for url in (link.url, link.fallback_url):
            parts = urlsplit(url)
            assert parts.netloc in {"www.booking.com", "www.airbnb.com"}
            assert not parts.fragment
        assert "evil.com" not in urlsplit(link.url).netloc
    booking, airbnb = build_links(evil, BOTH)
    assert _query(booking.url)["group_adults"] == ["1"]
    assert _query(booking.url)["ss"] == [evil.area]
    assert "%2F" in urlsplit(airbnb.url).path


def test_without_area_or_price_links_still_build() -> None:
    bare = SearchInput(date(2026, 11, 6), date(2026, 11, 7), ())
    booking, airbnb = build_links(bare, BOTH)
    assert "ss" not in _query(booking.url)
    assert "nflt" not in _query(booking.url)
    assert booking.fallback_url == "https://www.booking.com/"
    assert urlsplit(airbnb.url).path == "/s/homes"
    assert _query(booking.url)["group_adults"] == ["1"]


def test_links_are_deterministic_and_fall_back_without_filters() -> None:
    assert build_links(FAMILY, BOTH) == build_links(FAMILY, BOTH)
    booking, airbnb = build_links(FAMILY, BOTH)
    assert "checkin" not in booking.fallback_url
    assert "checkin" not in airbnb.fallback_url
    assert _query(booking.fallback_url) == {"ss": ["Kraków"]}


def test_only_allowed_platforms_get_a_link() -> None:
    links = build_links(FAMILY, frozenset({Platform.AIRBNB}))
    assert [link.platform for link in links] == [Platform.AIRBNB]


def test_nightly_cap_prefers_the_daily_limit() -> None:
    assert nightly_cap(Decimal("600.90"), Decimal(9000), 3) == (600, "budget_day_max")
    assert nightly_cap(None, Decimal(1700), 3) == (
        566,
        "budget_total_max_per_night",
    )
    assert nightly_cap(None, None, 3) == (None, None)


def _trip(**over: object) -> TripRead:
    data: dict[str, Any] = {
        "id": TRIP,
        "name": "Test",
        "destination": "Kraków",
        "created_at": "2026-10-03T10:00:00Z",
        "start_date": date(2026, 11, 6),
        "end_date": date(2026, 11, 9),
        "day_start": "09:00",
        "day_end": "19:00",
        "city_slug": None,
        "currency": "PLN",
        "budget_total_min": None,
        "budget_total_max": None,
        "budget_day_min": None,
        "budget_day_max": Decimal(600),
        "budget_flex_pct": 10,
        "fairness_alpha": 1.0,
        "my_role": TripRole.HOST,
        "my_status": MemberStatus.CONFIRMED,
    }
    return TripRead.model_validate(data | over)


def _profile(age: int) -> ProfileRead:
    return ProfileRead.model_construct(age=age)


def _membership(role: TripRole = TripRole.HOST) -> TripMembership:
    return TripMembership(trip_id=TRIP, sub=BOB.sub, role=role)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    trip: TripRead,
    requirements: list[RequirementItem] | None = None,
    ages: tuple[int, ...] = (40, 38, 6, 13),
) -> None:
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=trip))
    monkeypatch.setattr(
        profile_service,
        "list_profiles",
        AsyncMock(return_value=[_profile(a) for a in ages]),
    )
    stored = RequirementsRead(requirements=requirements or [], version=3)
    monkeypatch.setattr(
        requirements_service, "get_requirements", AsyncMock(return_value=stored)
    )
    monkeypatch.setattr(place_service, "list_cities", AsyncMock(return_value=[]))


def _links() -> SearchLinksRead:
    return asyncio.run(
        search_links_service.get_search_links(AsyncMock(), _membership())
    )


def _only(platform: str, *, hard: bool = True) -> RequirementItem:
    return RequirementItem(kind=RequirementKind.PLATFORM, key=platform, hard=hard)


def test_service_builds_the_acceptance_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip())
    first = _links()
    second = _links()
    assert first == second
    assert (first.nights, first.adults, first.child_ages) == (3, 2, [13, 6])
    assert first.price_per_night is not None
    assert first.price_per_night.amount == 600
    assert first.price_per_night.basis == "budget_day_max"
    assert not first.platforms_restricted
    assert all(not p.official for p in first.links[1].params)
    assert any(not p.official for p in first.links[0].params)


def test_only_airbnb_requirement_drops_booking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip(), [_only("airbnb")])
    result = _links()
    assert [link.platform for link in result.links] == [Platform.AIRBNB]
    assert result.platforms_restricted
    assert result.requirements_version == 3


def test_soft_platform_requirement_keeps_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip(), [_only("airbnb", hard=False)])
    result = _links()
    assert len(result.links) == 2


def test_city_gives_area_and_currency(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        _trip(city_slug="berlin", currency=None, destination=None),
    )
    city = CityRead.model_construct(slug="berlin", name="Berlin", currency="EUR")
    monkeypatch.setattr(place_service, "list_cities", AsyncMock(return_value=[city]))
    result = _links()
    assert result.area == "Berlin"
    assert result.price_per_night is not None
    assert result.price_per_night.currency == "EUR"


def test_no_budget_or_currency_means_no_price_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip(budget_day_max=None, currency=None))
    result = _links()
    assert result.price_per_night is None
    assert all(
        "nflt" not in p.name and "price_max" not in p.name
        for link in result.links
        for p in link.params
    )


@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": None, "end_date": None},
        {"start_date": date(2026, 11, 6), "end_date": date(2026, 11, 6)},
    ],
)
def test_trip_without_a_night_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, dates: dict[str, date | None]
) -> None:
    _patch(monkeypatch, _trip(**dates))
    with pytest.raises(search_links_service.SearchLinksUnavailableError):
        _links()


class _Session:
    commits = 0

    async def commit(self) -> None:
        self.commits += 1


def test_opening_is_logged_with_params_and_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip())
    saved: list[SearchOpening] = []

    def insert(_s: object, opening: SearchOpening) -> None:
        opening.id = uuid.uuid4()
        opening.opened_at = datetime.now(UTC)
        saved.append(opening)

    monkeypatch.setattr(db, "insert_opening", AsyncMock(side_effect=insert))
    session = _Session()
    entry = asyncio.run(
        search_links_service.record_opening(
            session,  # ty: ignore[invalid-argument-type]
            _membership(),
            Platform.BOOKING,
        )
    )
    assert session.commits == 1
    assert entry.actor_sub == BOB.sub
    assert entry.platform is Platform.BOOKING
    assert {"name": "group_adults", "value": "2", "official": True} in saved[0].params
    assert entry.url.startswith("https://www.booking.com/searchresults.html?")


def test_excluded_platform_cannot_be_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip(), [_only("airbnb")])
    with pytest.raises(search_links_service.PlatformNotAllowedError):
        asyncio.run(
            search_links_service.record_opening(
                _Session(),  # ty: ignore[invalid-argument-type]
                _membership(),
                Platform.BOOKING,
            )
        )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole,
    grants: tuple[Grant, ...] | None = None,
) -> TestClient:
    def get_membership(
        _session: object, _trip: uuid.UUID, _sub: str, min_role: TripRole
    ) -> TripMembership:
        if not role.satisfies(min_role):
            msg = f"Trip role '{min_role}' required (you are '{role}')"
            raise trip_service.TripRoleError(msg)
        return _membership(role)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=get_membership)
    )
    app = create_app()
    if grants is None:
        authorize(app, BOB)
    else:
        authorize(app, BOB, grants)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_member_reads_links_but_only_host_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip())
    with _client(monkeypatch, TripRole.MEMBER) as client:
        response = client.get(path("get_search_links", trip_id=TRIP))
        assert response.status_code == 200
        body = SearchLinksRead.model_validate(response.json())
        assert len(body.links) == 2
        post = client.post(
            path("post_search_opened", trip_id=TRIP), json={"platform": "booking"}
        )
        assert post.status_code == 403
    with _client(monkeypatch, TripRole.CO_HOST) as client:
        post = client.post(
            path("post_search_opened", trip_id=TRIP), json={"platform": "booking"}
        )
        assert post.status_code == 403


def test_endpoints_need_the_accommodation_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, TripRole.HOST, ()) as client:
        get = client.get(path("get_search_links", trip_id=TRIP))
        assert get.status_code == 403
        assert get.json()["detail"] == "Missing permission accommodation:READ"
        log = client.get(path("list_search_openings", trip_id=TRIP))
        assert log.status_code == 403
    read_only = (Grant(Feature.ACCOMMODATION, Access.READ),)
    with _client(monkeypatch, TripRole.HOST, read_only) as client:
        post = client.post(
            path("post_search_opened", trip_id=TRIP), json={"platform": "booking"}
        )
        assert post.status_code == 403
        assert post.json()["detail"] == "Missing permission accommodation:WRITE"


def test_trip_without_dates_is_422_and_outsider_gets_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _trip(start_date=None, end_date=None))
    with _client(monkeypatch, TripRole.MEMBER) as client:
        assert client.get(path("get_search_links", trip_id=TRIP)).status_code == 422
    error = TripNotFoundError(str(TRIP))
    monkeypatch.setattr(trip_service, "get_membership", AsyncMock(side_effect=error))
    app = create_app()
    authorize(app, BOB)
    app.dependency_overrides[get_session] = lambda: None
    with TestClient(app) as client:
        assert client.get(path("get_search_links", trip_id=TRIP)).status_code == 404
        assert client.get(path("list_search_openings", trip_id=TRIP)).status_code == 404
