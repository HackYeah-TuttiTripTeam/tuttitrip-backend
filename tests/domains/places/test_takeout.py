"""Google Takeout list: CSV reading, matching by name and the endpoint."""

import uuid
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.places.takeout.logic.name_match import (
    Candidate,
    MatchOutcome,
    match_title,
    normalise,
)
from tuttitrip.places.takeout.logic.takeout_csv import (
    MAX_ROWS,
    TakeoutFormatError,
    parse_takeout,
)
from tuttitrip.places.takeout.schemas import TakeoutImportRead
from tuttitrip.places.takeout.services import takeout_service
from tuttitrip.places.takeout.services.takeout_service import TakeoutTripError
from tuttitrip.profiles.feedback.services.feedback_service import ProfileNotFoundError
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

BOB = AuthenticatedUser(sub="auth0|bob")
TRIP = uuid.UUID("00000000-0000-4000-8000-000000000001")
PROFILE = uuid.UUID("00000000-0000-4000-8000-000000000002")
WAWEL = Candidate(uuid.UUID(int=1), "Zamek Królewski na Wawelu")
SUKIENNICE = Candidate(uuid.UUID(int=2), "Sukiennice")
BARS = (
    Candidate(uuid.UUID(int=3), "Bar Mleczny Rynek"),
    Candidate(uuid.UUID(int=4), "Bar Mleczny Pod Wawelem"),
)

EN = "Title,Note,URL,Tags,Comment\nWawel,,https://maps.google.com/?cid=1,,\nSukiennice,x,,,\n"
PL = "Tytuł,Notatka,Adres URL,Tagi,Komentarz\nSukiennice,,,,\n"


# --- the CSV ----------------------------------------------------------------------


def test_reads_the_english_and_the_polish_header() -> None:
    assert [r.title for r in parse_takeout(EN.encode())] == ["Wawel", "Sukiennice"]
    assert [r.title for r in parse_takeout(PL.encode())] == ["Sukiennice"]


def test_lines_are_numbered_like_the_file_and_a_bom_is_ignored() -> None:
    rows = parse_takeout(("﻿" + EN).encode())
    assert [r.line for r in rows] == [2, 3]


def test_an_empty_title_is_read_from_the_maps_link() -> None:
    url = "https://www.google.com/maps/place/Zamek+Kr%C3%B3lewski/data=!4m2"
    rows = parse_takeout(f"Title,URL\n,{url}\n,https://example.com/x\n".encode())
    assert [r.title for r in rows] == ["Zamek Królewski", ""]


def test_blank_lines_are_skipped() -> None:
    rows = parse_takeout(b"Title,Note\n\nA,\n,\nB,\n")
    assert [r.title for r in rows] == ["A", "B"]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"Name,Note\nA,\n",
        b"Title,Note\n",
        b"\xff\xfe\x00bad",
        b"Title\n" + b"A\n" * (MAX_ROWS + 1),
    ],
)
def test_other_files_are_refused(data: bytes) -> None:
    with pytest.raises(TakeoutFormatError):
        parse_takeout(data)


# --- the match --------------------------------------------------------------------


def test_names_are_compared_without_case_diacritics_or_punctuation() -> None:
    assert normalise("  Zamek Królewski, Wawel! ") == "zamek krolewski wawel"
    assert normalise("Łódź") == "lodz"


def test_an_exact_name_wins_and_a_contained_name_matches_when_alone() -> None:
    assert (
        match_title("sukiennice", [WAWEL, SUKIENNICE]).place_id == SUKIENNICE.place_id
    )
    found = match_title("Zamek Królewski", [WAWEL, SUKIENNICE])
    assert found.outcome is MatchOutcome.MATCHED
    assert found.place_id == WAWEL.place_id


def test_two_equally_good_places_are_ambiguous_and_never_guessed() -> None:
    found = match_title("Bar Mleczny", BARS)
    assert found.outcome is MatchOutcome.AMBIGUOUS
    assert found.place_id is None


def test_an_exact_name_beats_a_partial_one() -> None:
    exact = Candidate(uuid.UUID(int=5), "Bar Mleczny")
    assert match_title("bar mleczny", [exact, *BARS]).place_id == exact.place_id


def test_an_unknown_title_is_not_in_the_catalog() -> None:
    assert match_title("Zoo", [WAWEL]).outcome is MatchOutcome.NOT_IN_CATALOG
    assert match_title("???", [WAWEL]).outcome is MatchOutcome.NOT_IN_CATALOG


# --- the endpoint -----------------------------------------------------------------


def _client(
    monkeypatch: pytest.MonkeyPatch,
    role: TripRole = TripRole.CO_HOST,
    grants: tuple[Grant, ...] | None = None,
) -> TestClient:
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(return_value=TripMembership(trip_id=TRIP, sub=BOB.sub, role=role)),
    )
    app = create_app()
    if grants is None:
        authorize(app, BOB)
    else:
        authorize(app, BOB, grants)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def _post(client: TestClient, data: bytes = EN.encode()) -> httpx2.Response:
    return client.post(
        path("import_takeout", trip_id=TRIP),
        files={"file": ("Saved.csv", data, "text/csv")},
        data={"profile_id": str(PROFILE)},
    )


def test_the_import_returns_what_the_service_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read = TakeoutImportRead(
        profile_id=PROFILE,
        city_slug="krakow",
        total=1,
        liked=0,
        kept=0,
        matched=[],
        unmatched=[],
    )
    service = AsyncMock(return_value=read)
    monkeypatch.setattr(takeout_service, "import_takeout", service)
    response = _post(_client(monkeypatch))
    assert response.status_code == 200
    assert response.json()["city_slug"] == "krakow"
    assert service.await_args is not None
    assert service.await_args.args[2:] == (PROFILE, EN.encode())


def test_a_wrong_file_is_422_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _post(_client(monkeypatch), b"Name\nx\n")
    assert response.status_code == 422
    assert "Title" in response.json()["detail"]


def test_a_trip_without_a_city_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        takeout_service, "import_takeout", AsyncMock(side_effect=TakeoutTripError("x"))
    )
    assert _post(_client(monkeypatch)).status_code == 422


def test_an_unknown_profile_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        takeout_service,
        "import_takeout",
        AsyncMock(side_effect=ProfileNotFoundError("x")),
    )
    assert _post(_client(monkeypatch)).status_code == 404


def test_the_import_needs_the_feedback_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(
        monkeypatch, grants=(Grant(Feature.PROFILES_FEEDBACK, Access.READ),)
    )
    assert _post(client).status_code == 403
