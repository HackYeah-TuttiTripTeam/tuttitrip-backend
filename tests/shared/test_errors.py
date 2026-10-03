"""Validation errors never echo request values."""

from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser


def test_422_keeps_the_usual_shape_without_input_or_ctx() -> None:
    app = create_app()
    authorize(app, AuthenticatedUser(sub="auth0|bob"))
    secret = "s3cret-" + "x" * 300
    response = TestClient(app).post(path("create_trip"), json={"name": secret})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert errors
    for error in errors:
        assert set(error) <= {"type", "loc", "msg"}
    assert "s3cret" not in response.text
