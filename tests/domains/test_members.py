"""Trip members: the role matrix and the member routes."""

import asyncio
import uuid
from collections.abc import Coroutine, Iterator
from dataclasses import dataclass, field, fields
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles import db as profiles_db
from tuttitrip.profiles.logic.age_defaults import DEFAULTS, age_group_for
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.trips import db as trips_db
from tuttitrip.trips.logic.member_rules import can_remove, can_set_role
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import member_service, trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

HOST, CO_HOST, MEMBER = TripRole.HOST, TripRole.CO_HOST, TripRole.MEMBER
TRIP = uuid.uuid4()

# (actor, target) -> may remove. Nobody removes the host or themselves.
REMOVE = {
    (HOST, HOST): False,
    (HOST, CO_HOST): True,
    (HOST, MEMBER): True,
    (CO_HOST, HOST): False,
    (CO_HOST, CO_HOST): False,
    (CO_HOST, MEMBER): True,
    (MEMBER, HOST): False,
    (MEMBER, CO_HOST): False,
    (MEMBER, MEMBER): False,
}


@pytest.mark.parametrize(("actor", "target"), list(product(TripRole, TripRole)))
def test_remove_matrix(actor: TripRole, target: TripRole) -> None:
    assert can_remove(actor, target) is REMOVE[actor, target]


@pytest.mark.parametrize(
    ("actor", "target", "new"), list(product(TripRole, TripRole, TripRole))
)
def test_set_role_matrix(actor: TripRole, target: TripRole, new: TripRole) -> None:
    expected = actor is HOST and target is not HOST and new is not HOST
    assert can_set_role(actor, target, new) is expected


@dataclass
class Trip:
    """In-memory state behind the mocked database layers."""

    roles: dict[str, TripRole] = field(default_factory=dict)
    profiles: dict[uuid.UUID, Profile] = field(default_factory=dict)

    def add(self, name: str, role: TripRole | None, sub: str | None = None) -> Profile:
        group = DEFAULTS[age_group_for(30)]
        profile = Profile(
            id=uuid.uuid4(),
            trip_id=TRIP,
            display_name=name,
            age=30,
            user_sub=sub,
            weight=1.0,
            **{f.name: getattr(group, f.name) for f in fields(group)},
        )
        self.profiles[profile.id] = profile
        if role is not None and sub is not None:
            self.roles[sub] = role
        return profile


ME = AuthenticatedUser(sub="auth0|me")


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> Trip:
    trip = Trip()

    def membership(
        _session: object, trip_id: uuid.UUID, sub: str, min_role: TripRole
    ) -> TripMembership:
        role = trip.roles.get(sub)
        if role is None:
            raise TripNotFoundError(str(trip_id))
        if not role.satisfies(min_role):
            raise trip_service.TripRoleError(str(min_role))
        return TripMembership(trip_id=trip_id, sub=sub, role=role)

    def delete_member(_s: object, _t: uuid.UUID, sub: str) -> None:
        del trip.roles[sub]

    def update_role(_s: object, _t: uuid.UUID, sub: str, role: TripRole) -> None:
        trip.roles[sub] = role

    def select_profile(
        _s: object, _t: uuid.UUID, profile_id: uuid.UUID
    ) -> Profile | None:
        return trip.profiles.get(profile_id)

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        trips_db, "select_member_roles", AsyncMock(side_effect=lambda *_: trip.roles)
    )
    monkeypatch.setattr(
        trips_db,
        "select_member_role",
        AsyncMock(side_effect=lambda _s, _t, sub: trip.roles.get(sub)),
    )
    monkeypatch.setattr(trips_db, "delete_member", AsyncMock(side_effect=delete_member))
    monkeypatch.setattr(
        trips_db, "update_member_role", AsyncMock(side_effect=update_role)
    )
    monkeypatch.setattr(
        profile_service.db, "select_profile", AsyncMock(side_effect=select_profile)
    )
    monkeypatch.setattr(
        profile_service.db,
        "select_profiles_by_trip",
        AsyncMock(
            side_effect=lambda *_: sorted(
                trip.profiles.values(), key=lambda p: p.display_name
            )
        ),
    )
    return trip


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def app(session: AsyncMock) -> FastAPI:
    application = create_app()
    authorize(application, ME)
    application.dependency_overrides[get_session] = lambda: session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _as(state: Trip, role: TripRole) -> None:
    state.add("Ja", role, ME.sub)


def _member_path(name: str, profile: Profile) -> str:
    return path(name, trip_id=TRIP, profile_id=profile.id)


def test_list_shows_names_from_profiles_and_skips_accountless(
    client: TestClient, state: Trip
) -> None:
    _as(state, MEMBER)
    state.add("Ola", HOST, "auth0|ola")
    state.add("Kuba", CO_HOST, "auth0|kuba")
    state.add("Zosia", None)  # a profile without an account is not a member
    response = client.get(path("list_members", trip_id=TRIP))
    assert response.status_code == 200
    body = response.json()
    assert [(m["display_name"], m["role"], m["is_me"]) for m in body] == [
        ("Ola", "host", False),
        ("Kuba", "co_host", False),
        ("Ja", "member", True),
    ]
    assert "user_sub" not in body[0]


def test_outsider_gets_404_on_every_member_route(
    client: TestClient, state: Trip
) -> None:
    other = state.add("Ola", HOST, "auth0|ola")
    assert client.get(path("list_members", trip_id=TRIP)).status_code == 404
    patch = client.patch(_member_path("update_member", other), json={"role": "member"})
    assert patch.status_code == 404
    assert client.delete(_member_path("remove_member", other)).status_code == 404


def test_host_makes_a_member_co_host_and_back(
    client: TestClient, state: Trip, session: AsyncMock
) -> None:
    _as(state, HOST)
    kuba = state.add("Kuba", MEMBER, "auth0|kuba")
    response = client.patch(
        _member_path("update_member", kuba), json={"role": "co_host"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "co_host"
    assert state.roles["auth0|kuba"] is CO_HOST
    session.commit.assert_awaited_once()
    client.patch(_member_path("update_member", kuba), json={"role": "member"})
    assert state.roles["auth0|kuba"] is MEMBER


@pytest.mark.parametrize("caller", [CO_HOST, MEMBER])
def test_only_the_host_changes_roles(
    client: TestClient, state: Trip, session: AsyncMock, caller: TripRole
) -> None:
    _as(state, caller)
    kuba = state.add("Kuba", MEMBER, "auth0|kuba")
    response = client.patch(
        _member_path("update_member", kuba), json={"role": "co_host"}
    )
    assert response.status_code == 403
    assert state.roles["auth0|kuba"] is MEMBER
    session.commit.assert_not_awaited()


def test_host_cannot_demote_self_or_hand_over_the_host_role(
    client: TestClient, state: Trip
) -> None:
    mine = state.add("Ja", HOST, ME.sub)
    kuba = state.add("Kuba", MEMBER, "auth0|kuba")
    demote = client.patch(_member_path("update_member", mine), json={"role": "member"})
    assert demote.status_code == 403
    handover = client.patch(_member_path("update_member", kuba), json={"role": "host"})
    assert handover.status_code == 422
    assert state.roles[ME.sub] is HOST
    assert state.roles["auth0|kuba"] is MEMBER


@pytest.mark.parametrize(("caller", "target"), list(product(TripRole, TripRole)))
def test_delete_follows_the_matrix(
    client: TestClient,
    state: Trip,
    session: AsyncMock,
    caller: TripRole,
    target: TripRole,
) -> None:
    _as(state, caller)
    victim = state.add("Ofiara", target, "auth0|victim")
    response = client.delete(_member_path("remove_member", victim))
    # A plain member stops at the TripCoHost guard, before the member rules.
    expected = 204 if caller is not MEMBER and REMOVE[caller, target] else 403
    assert response.status_code == expected
    assert ("auth0|victim" in state.roles) is (expected != 204)
    assert session.commit.await_count == (1 if expected == 204 else 0)


def test_removed_member_loses_access_but_keeps_the_profile(
    client: TestClient, state: Trip
) -> None:
    _as(state, HOST)
    kuba = state.add("Kuba", MEMBER, "auth0|kuba")
    assert client.delete(_member_path("remove_member", kuba)).status_code == 204
    # No longer a member: TripAccess answers 404 for them.
    assert "auth0|kuba" not in state.roles
    # The person stays on the trip as a profile without an account.
    assert state.profiles[kuba.id].user_sub is None
    assert state.profiles[kuba.id].display_name == "Kuba"
    listed = client.get(path("list_members", trip_id=TRIP)).json()
    assert [m["display_name"] for m in listed] == ["Ja"]
    # ...and is no longer addressable as a member.
    assert client.delete(_member_path("remove_member", kuba)).status_code == 404


def test_removed_member_gets_404_on_the_trip(
    app: FastAPI, client: TestClient, state: Trip
) -> None:
    _as(state, HOST)
    kuba = state.add("Kuba", MEMBER, "auth0|kuba")
    client.delete(_member_path("remove_member", kuba))
    authorize(app, AuthenticatedUser(sub="auth0|kuba"))
    assert client.get(path("get_trip", trip_id=TRIP)).status_code == 404


def test_unknown_or_accountless_profile_is_404(client: TestClient, state: Trip) -> None:
    _as(state, HOST)
    zosia = state.add("Zosia", None)
    assert client.delete(_member_path("remove_member", zosia)).status_code == 404
    ghost = Profile(id=uuid.uuid4())
    assert client.delete(_member_path("remove_member", ghost)).status_code == 404


def test_403_names_roles_as_plain_values(client: TestClient, state: Trip) -> None:
    _as(state, CO_HOST)
    boss = state.add("Ola", HOST, "auth0|ola")
    detail = client.delete(_member_path("remove_member", boss)).json()["detail"]
    assert detail == "A co_host cannot remove a host"


def test_403_on_role_change_names_roles_as_plain_values(
    client: TestClient, state: Trip
) -> None:
    mine = state.add("Ja", HOST, ME.sub)
    response = client.patch(
        _member_path("update_member", mine), json={"role": "co_host"}
    )
    assert response.json()["detail"] == "A host cannot change a host to co_host"


# The /profiles routes must not be a second way to change membership.
@pytest.mark.parametrize(
    ("caller", "target"),
    [(CO_HOST, HOST), (CO_HOST, CO_HOST), (HOST, HOST), (HOST, MEMBER)],
)
def test_profiles_routes_refuse_to_unlink_or_delete_an_account_profile(
    client: TestClient,
    state: Trip,
    session: AsyncMock,
    caller: TripRole,
    target: TripRole,
) -> None:
    mine = state.add("Ja", caller, ME.sub)
    victim = mine if caller is target else state.add("Ola", target, "auth0|ola")
    sub = victim.user_sub
    unlink = client.patch(
        path("update_profile", trip_id=TRIP, profile_id=victim.id),
        json={"user_sub": None},
    )
    delete = client.delete(path("delete_profile", trip_id=TRIP, profile_id=victim.id))
    assert (unlink.status_code, delete.status_code) == (409, 409)
    assert "DELETE /trips/{trip_id}/members/{profile_id}" in delete.json()["detail"]
    assert victim.user_sub == sub
    assert victim.id in state.profiles
    session.commit.assert_not_awaited()


def test_accountless_profile_can_still_be_deleted(
    client: TestClient,
    state: Trip,
    session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _as(state, HOST)
    zosia = state.add("Zosia", None)
    monkeypatch.setattr(profile_service.db, "delete_profile", AsyncMock())
    response = client.delete(path("delete_profile", trip_id=TRIP, profile_id=zosia.id))
    assert response.status_code == 204
    session.commit.assert_awaited_once()


def _run(coro: Coroutine[object, object, object]) -> None:
    asyncio.run(coro)


def _sql(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))  # ty: ignore[unresolved-attribute]


def _clauses(*names: str) -> list[str]:
    return [f"{name} = %(" for name in names]


def test_member_queries_are_scoped_to_the_trip() -> None:
    session = AsyncMock()
    rows = MagicMock()
    rows.tuples.return_value.all.return_value = []
    session.execute.return_value = rows
    sub, role = "auth0|x", TripRole.MEMBER

    async def run() -> None:
        await trips_db.select_member_roles(session, TRIP)
        await trips_db.delete_member(session, TRIP, sub)
        await trips_db.update_member_role(session, TRIP, sub, role)

    _run(run())
    roles, delete, update = (_sql(c.args[0]) for c in session.execute.call_args_list)
    assert "trip_members.trip_id = %(" in roles
    for statement in (delete, update):
        for clause in _clauses("trip_members.trip_id", "trip_members.user_sub"):
            assert clause in statement
    assert update.startswith("UPDATE trip_members")
    assert delete.startswith("DELETE FROM trip_members")


def test_profile_lookup_is_scoped_to_the_trip() -> None:
    session = AsyncMock()
    _run(profiles_db.select_profile(session, TRIP, uuid.uuid4()))
    statement = _sql(session.scalar.call_args.args[0])
    for clause in _clauses("profiles.id", "profiles.trip_id"):
        assert clause in statement


def test_remove_member_deletes_the_row_then_clears_user_sub_then_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    profile = Profile(id=uuid.uuid4(), trip_id=TRIP, user_sub="auth0|kuba", age=30)
    session.scalar.side_effect = [profile, TripRole.MEMBER, profile]
    monkeypatch.setattr(
        profile_service.ProfileRead,
        "model_validate",
        lambda p: SimpleNamespace(id=p.id, user_sub=p.user_sub),
    )
    host = TripMembership(trip_id=TRIP, sub=ME.sub, role=HOST)
    _run(member_service.remove_member(session, host, profile.id))
    calls = [c[0] for c in session.method_calls]
    assert calls == ["scalar", "scalar", "execute", "scalar", "flush", "commit"]
    assert _sql(session.execute.call_args.args[0]).startswith(
        "DELETE FROM trip_members"
    )
    assert profile.user_sub is None
