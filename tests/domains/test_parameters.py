"""Admin parameters of the algorithm: ranges, guards and what a plan records."""

from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.planning.logic.params import DEFAULT_PARAMS, AlgorithmParams
from tuttitrip.planning.parameters.schemas import ParametersRead
from tuttitrip.planning.parameters.services import parameters_service
from tuttitrip.planning.plans.logic.input_builder import input_hash
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature

ADMIN = AuthenticatedUser(sub="auth0|admin")
URL = path("get_parameters")
VERSIONS = path("list_versions")


def _client(grants: tuple[Grant, ...] | None = None) -> TestClient:
    app = create_app()
    if grants is None:
        authorize(app, ADMIN)
    else:
        authorize(app, ADMIN, grants)
    app.dependency_overrides[get_session] = lambda: None
    return TestClient(app)


def test_defaults_are_the_specification_table() -> None:
    assert DEFAULT_PARAMS.alpha == pytest.approx(1.0)
    assert DEFAULT_PARAMS.strong_preference == pytest.approx(0.4)
    assert DEFAULT_PARAMS.violation_penalty == pytest.approx(1000.0)
    assert AlgorithmParams(**asdict(DEFAULT_PARAMS)) == DEFAULT_PARAMS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("alpha", 3.1),
        ("alpha", -0.1),
        ("strong_preference", 1.0),
        ("tau_ref_min", 5),
        ("vote_weight", 1.5),
        ("smoothing", 0),
        ("verdict_iconic", 0.5),  # not below verdict_fits
    ],
)
def test_value_outside_the_range_is_refused(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        AlgorithmParams(**{field: value})  # ty: ignore[invalid-argument-type]


def test_unknown_parameter_is_refused() -> None:
    with pytest.raises(ValidationError):
        AlgorithmParams(gamma=1.0)  # ty: ignore[unknown-argument]


def test_post_out_of_range_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    create = AsyncMock()
    monkeypatch.setattr(parameters_service, "create_version", create)
    with _client() as client:
        response = client.post(URL, json={"values": {"alpha": 4}})
    assert response.status_code == pytest.approx(422)
    create.assert_not_awaited()
    assert "4" not in str(response.json().get("detail", [{}])[0].get("input", ""))


def test_post_stores_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = ParametersRead(
        version=2, values=AlgorithmParams(strong_preference=0.5), note="test"
    )
    create = AsyncMock(return_value=stored)
    monkeypatch.setattr(parameters_service, "create_version", create)
    with _client() as client:
        response = client.post(
            URL, json={"values": {"strong_preference": 0.5}, "note": "test"}
        )
    assert response.status_code == pytest.approx(201)
    assert response.json()["version"] == 2
    assert response.json()["values"]["strong_preference"] == pytest.approx(0.5)
    assert create.await_args is not None
    data = create.await_args.args[2]
    assert data.values.strong_preference == pytest.approx(0.5)
    assert data.values.alpha == pytest.approx(DEFAULT_PARAMS.alpha)


def test_regular_user_gets_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parameters_service, "read_current", AsyncMock())
    user_grants = (Grant(Feature.TRIPS_CORE, Access.WRITE),)
    with _client(user_grants) as client:
        assert client.get(URL).status_code == pytest.approx(403)
        assert client.post(URL, json={}).status_code == pytest.approx(403)
        assert client.get(VERSIONS).status_code == pytest.approx(403)


def test_get_without_a_stored_version_is_version_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parameters_service,
        "read_current",
        AsyncMock(return_value=ParametersRead(version=0, values=DEFAULT_PARAMS)),
    )
    with _client() as client:
        body = client.get(URL).json()
    assert body["version"] == 0
    assert body["values"]["alpha"] == pytest.approx(1.0)


def test_a_new_theta_changes_the_plan_hash() -> None:
    data = planning_input(reference(), lodging=False)
    base = input_hash(data, 1.0, "default", DEFAULT_PARAMS, 0)
    changed = AlgorithmParams(strong_preference=0.5)
    assert base != input_hash(data, 1.0, "default", changed, 1)
    assert base != input_hash(data, 1.0, "default", DEFAULT_PARAMS, 1)
