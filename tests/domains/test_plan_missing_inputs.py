"""A plan asked for on incomplete data answers what is missing (backend#219)."""

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.city import city
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tests.shared.fakes import authorize
from tests.shared.interview_world import World
from tests.shared.paths import path
from tuttitrip.interview.schemas import CardKind, QuestionField
from tuttitrip.interview.services import draft_plan_service
from tuttitrip.main import create_app
from tuttitrip.places.services import place_service
from tuttitrip.planning.plans.logic.input_builder import (
    CatalogEmptyError,
    MissingInputsError,
    PlanInputError,
    build_input,
    find_missing,
)
from tuttitrip.planning.plans.schemas import (
    MissingCard,
    MissingField,
    PlanAssumptions,
    PlanErrorCode,
)
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.profiles.feedback.schemas import TripFeedback
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

ME = AuthenticatedUser(sub="auth0|host")
ALL_MISSING = [
    {"field": "destination", "person_id": None, "kind": "city", "options": []},
    {"field": "dates", "person_id": None, "kind": "date_range", "options": []},
    {"field": "people", "person_id": None, "kind": "family_builder", "options": []},
]


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def client(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = lambda: world.session
    monkeypatch.setattr(
        trip_service,
        "get_membership",
        AsyncMock(
            return_value=TripMembership(
                trip_id=world.trip_id, sub=ME.sub, role=TripRole.HOST
            )
        ),
    )
    with TestClient(app) as test_client:
        yield test_client


async def gather(world: World, monkeypatch: pytest.MonkeyPatch, **trip: Any) -> Any:  # ruff: ignore[any-type]
    """``gather_input`` over a trip with the given fields and no people."""
    world.trip = world.trip.model_copy(update=trip)
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=world.trip))
    monkeypatch.setattr(place_service, "list_cities", AsyncMock(return_value=[city()]))
    monkeypatch.setattr(profile_service, "list_profiles", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        preference_service, "list_preferences", AsyncMock(return_value=[])
    )
    return await plan_service.gather_input(world.session, world.membership)


# --- what is missing -----------------------------------------------------------


def test_everything_missing_is_listed_at_once_in_the_order_of_the_interview(
    world: World,
) -> None:
    missing = find_missing(world.trip, city_known=False, people=0)
    assert [(m.field, m.kind) for m in missing] == [
        (MissingField.DESTINATION, MissingCard.CITY),
        (MissingField.DATES, MissingCard.DATE_RANGE),
        (MissingField.PEOPLE, MissingCard.FAMILY_BUILDER),
    ]


def test_a_complete_trip_misses_nothing(world: World) -> None:
    complete = world.trip.model_copy(
        update={
            "start_date": world.trip.created_at.date(),
            "end_date": world.trip.created_at.date(),
        }
    )
    assert find_missing(complete, city_known=True, people=3) == []


def test_one_date_is_not_enough(world: World) -> None:
    half = world.trip.model_copy(update={"start_date": world.trip.created_at.date()})
    fields = [m.field for m in find_missing(half, city_known=True, people=2)]
    assert fields == [MissingField.DATES]


def test_the_cards_and_fields_are_those_of_the_interview() -> None:
    assert {f.value for f in MissingField} <= {f.value for f in QuestionField}
    assert {c.value for c in MissingCard} <= {c.value for c in CardKind}


def test_build_input_names_what_the_trip_lacks(world: World) -> None:
    with pytest.raises(MissingInputsError) as caught:
        build_input(
            world.trip.model_copy(update={"end_date": None}),
            city=city(),
            profiles=[],
            preferences=[],
            feedback=TripFeedback(ratings=[], vetoes=[]),
            places=[],
        )
    assert [m.field for m in caught.value.missing] == [
        MissingField.DATES,
        MissingField.PEOPLE,
    ]
    assert isinstance(caught.value, PlanInputError)


def planning_feedback() -> Any:  # ruff: ignore[any-type]
    from tuttitrip.profiles.feedback.schemas import TripFeedback  # ruff: ignore[import-outside-top-level] only this test needs it

    return TripFeedback(ratings=[], vetoes=[])


def test_gathering_reports_every_gap_not_only_the_first(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(MissingInputsError) as caught:
        asyncio.run(gather(world, monkeypatch))
    assert [m.field for m in caught.value.missing] == list(MissingField)
    assert "destination, dates, people" in caught.value.message


def test_a_city_outside_the_catalog_is_a_missing_destination(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(MissingInputsError) as caught:
        asyncio.run(gather(world, monkeypatch, city_slug="atlantis"))
    assert caught.value.missing[0].field is MissingField.DESTINATION
    assert caught.value.message == "Unknown city 'atlantis'"


def test_a_known_city_with_no_dates_does_not_blame_the_city(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(MissingInputsError) as caught:
        asyncio.run(gather(world, monkeypatch, city_slug=city().slug))
    assert [m.field for m in caught.value.missing] == [
        MissingField.DATES,
        MissingField.PEOPLE,
    ]
    assert "Unknown city" not in caught.value.message


def test_a_draft_assumes_dates_and_people_so_only_the_city_can_be_missing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:

    assumptions = PlanAssumptions(
        start_date=world.trip.created_at.date(),
        end_date=world.trip.created_at.date(),
        min_people=2,
    )
    world.trip = world.trip.model_copy(update={"city_slug": None})
    monkeypatch.setattr(trip_service, "get_trip", AsyncMock(return_value=world.trip))
    monkeypatch.setattr(place_service, "list_cities", AsyncMock(return_value=[city()]))
    monkeypatch.setattr(profile_service, "list_profiles", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        preference_service, "list_preferences", AsyncMock(return_value=[])
    )
    with pytest.raises(MissingInputsError) as caught:
        asyncio.run(
            plan_service.gather_input(world.session, world.membership, assumptions)
        )
    assert [m.field for m in caught.value.missing] == [MissingField.DESTINATION]


def test_a_city_without_places_is_not_planned(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = planning_input(reference(), lodging=False).model_copy(update={"places": ()})
    monkeypatch.setattr(
        plan_service, "gather_input", AsyncMock(return_value=(empty, {}, 1.0))
    )
    with pytest.raises(CatalogEmptyError):
        asyncio.run(plan_service.generate_plan(world.session, world.membership, None))


# --- the answers ---------------------------------------------------------------


def test_post_plans_answers_422_with_the_list_of_missing_fields(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = MissingInputsError(find_missing(world.trip, city_known=False, people=0))
    monkeypatch.setattr(plan_service, "generate_plan", AsyncMock(side_effect=error))
    response = client.post(path("create_plan", trip_id=world.trip_id))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == PlanErrorCode.MISSING_INPUTS == "plan.missing_inputs"
    assert detail["missing"] == ALL_MISSING
    assert "destination" in detail["message"]


def test_post_plans_answers_409_for_a_city_without_places(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_service, "generate_plan", AsyncMock(side_effect=CatalogEmptyError())
    )
    response = client.post(path("create_plan", trip_id=world.trip_id))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "plan.catalog_empty"


def test_any_other_plan_input_error_stays_a_plain_422(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        plan_service, "generate_plan", AsyncMock(side_effect=PlanInputError("odd"))
    )
    response = client.post(path("create_plan", trip_id=world.trip_id))
    assert (response.status_code, response.json()["detail"]) == (422, "odd")


def test_the_draft_plan_maps_a_city_outside_the_catalog_and_an_empty_one(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        draft_plan_service,
        "build",
        AsyncMock(
            side_effect=MissingInputsError(
                find_missing(world.trip, city_known=False, people=2),
                "Unknown city 'x'",
            )
        ),
    )
    url = path("build_draft_plan", trip_id=world.trip_id)
    response = client.post(url)
    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "Unknown city 'x'"
    monkeypatch.setattr(
        draft_plan_service, "build", AsyncMock(side_effect=CatalogEmptyError())
    )
    assert client.post(url).status_code == 409


def test_openapi_documents_both_errors_of_both_endpoints() -> None:
    schema = create_app().openapi()
    for route in ("/trips/{trip_id}/plans", "/trips/{trip_id}/interview/draft-plan"):
        operation = schema["paths"][f"/api/v1{route}"]["post"]["responses"]
        refs = {
            code: operation[code]["content"]["application/json"]["schema"]["$ref"]
            for code in ("409", "422")
        }
        assert refs["422"].endswith("/PlanMissingInputs")
        assert refs["409"].endswith("/PlanCatalogEmpty")
    codes = schema["components"]["schemas"]["PlanMissingInputsDetail"]["properties"]
    assert codes["code"]["const"] == "plan.missing_inputs"
