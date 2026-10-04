"""Confirm, leave and the trip history against a real Postgres (local smoke step).

Needs `docker compose up -d --wait db && uv run alembic upgrade head`. The test
cleans up the trips it creates.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.shared.auth.schemas import AuthenticatedUser

# invitations/db.py still calls Result.tuples(), deprecated in SQLAlchemy 2.1.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SADeprecationWarning"),
]

RUN = uuid.uuid4().hex[:8]
HOST = AuthenticatedUser(sub=f"auth0|it-host-{RUN}")
GUEST = AuthenticatedUser(sub=f"auth0|it-guest-{RUN}")
NEXT = AuthenticatedUser(sub=f"auth0|it-next-{RUN}")


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
        authorize(app, HOST)
        for trip in test_client.get(path("list_trips"), params={"size": 100}).json()[
            "items"
        ]:
            test_client.delete(path("delete_trip", trip_id=trip["id"]))


def _as(app: FastAPI, user: AuthenticatedUser) -> None:
    authorize(app, user)


def _join(
    app: FastAPI, client: TestClient, trip_id: str, user: AuthenticatedUser
) -> str:
    _as(app, HOST)
    token = client.post(path("create_invitation", trip_id=trip_id), json={}).json()[
        "token"
    ]
    _as(app, user)
    joined = client.post(path("accept_invitation"), json={"token": token})
    assert joined.status_code == 200, joined.text
    return str(joined.json()["profile_id"])


@pytest.fixture
def trips(app: FastAPI, client: TestClient) -> dict[str, str]:
    today = datetime.now(UTC).date()
    _as(app, HOST)
    ids = {}
    for name, start, end in [
        ("past", today - timedelta(days=9), today - timedelta(days=7)),
        ("today", today, today),
        ("future", today + timedelta(days=5), today + timedelta(days=8)),
        ("undated", None, None),
    ]:
        body: dict[str, str | None] = {"name": f"{RUN} {name}"}
        if start and end:
            body |= {"start_date": start.isoformat(), "end_date": end.isoformat()}
        created = client.post(path("create_trip"), json=body)
        assert created.status_code == 201, created.text
        assert created.json()["my_status"] == "confirmed"
        ids[name] = str(created.json()["id"])
    return ids


def test_confirm_leave_claim_and_hand_over(
    app: FastAPI, client: TestClient, trips: dict[str, str]
) -> None:
    # A guest joins from an invitation: pending, then confirms.
    profile_id = _join(app, client, trips["future"], GUEST)
    mine = client.get(path("list_trips"), params={"status": "pending"}).json()
    assert [t["id"] for t in mine["items"]] == [trips["future"]]
    assert mine["items"][0]["my_status"] == "pending"
    confirmed = client.post(path("confirm_membership", trip_id=trips["future"]))
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    _as(app, HOST)
    members = client.get(path("list_members", trip_id=trips["future"])).json()
    assert {m["role"]: m["status"] for m in members} == {
        "host": "confirmed",
        "member": "confirmed",
    }

    # The host cannot leave; handing over the role unblocks it.
    left = client.post(path("leave_trip", trip_id=trips["future"]))
    assert left.status_code == 409
    _as(app, GUEST)
    leave = client.post(path("leave_trip", trip_id=trips["future"]))
    assert leave.status_code == 204
    assert client.get(path("get_trip", trip_id=trips["future"])).status_code == 404

    # The profile stays without an account and the next invitee can claim it.
    _as(app, HOST)
    token = client.post(path("create_invitation", trip_id=trips["future"]), json={})
    _as(app, NEXT)
    claimed = client.post(
        path("accept_invitation"),
        json={"token": token.json()["token"], "profile_id": profile_id},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["profile_id"] == profile_id
    assert claimed.json()["profile_claimed"] is True

    # Host hands over, then leaves.
    _as(app, HOST)
    handed = client.post(
        path("transfer_host", trip_id=trips["future"], profile_id=profile_id)
    )
    assert handed.status_code == 200
    assert handed.json()["role"] == "host"
    assert client.post(path("leave_trip", trip_id=trips["future"])).status_code == 204
    _as(app, NEXT)
    assert (
        client.get(path("get_trip", trip_id=trips["future"])).json()["my_role"]
        == "host"
    )
    client.delete(path("delete_trip", trip_id=trips["future"]))
