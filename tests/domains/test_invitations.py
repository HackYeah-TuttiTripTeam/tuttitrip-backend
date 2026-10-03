"""Trip invitations: issue, preview, join (with a profile), revoke."""

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles import db as profiles_db
from tuttitrip.profiles.db import link_account as real_link_account
from tuttitrip.profiles.models import Profile
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.services.token_service import hash_token
from tuttitrip.trips import db as trips_db
from tuttitrip.trips.invitations import db as inv_db
from tuttitrip.trips.invitations.models import TripInvitation
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service

TRIP = uuid.uuid4()
HOST = AuthenticatedUser(sub="auth0|host")
GUEST = AuthenticatedUser(sub="auth0|guest")
TOKEN = "t" * 43


@dataclass
class World:
    """In-memory state behind the mocked database layers."""

    roles: dict[str, TripRole] = field(default_factory=dict)
    profiles: list[Profile] = field(default_factory=list)
    invitations: dict[str, TripInvitation] = field(default_factory=dict)
    commits: int = 0

    def invite(self, **overrides: object) -> TripInvitation:
        values: dict[str, object] = {
            "id": uuid.uuid4(),
            "trip_id": TRIP,
            "token_hash": hash_token(TOKEN),
            "created_by_sub": HOST.sub,
            "created_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "max_uses": 5,
            "uses": 0,
            "revoked_at": None,
            "profile_id": None,
        } | overrides
        row = TripInvitation(**values)
        self.invitations[row.token_hash] = row
        return row

    def undo(self, sub: str) -> None:
        """What a rollback does to the account's rows."""
        self.roles.pop(sub, None)
        self.profiles[:] = [p for p in self.profiles if p.user_sub != sub]

    def membership(
        self, _s: object, trip_id: uuid.UUID, sub: str, min_role: TripRole
    ) -> TripMembership:
        role = self.roles.get(sub)
        if role is None:
            raise trip_service.TripNotFoundError(str(trip_id))
        if not role.satisfies(min_role):
            raise trip_service.TripRoleError(str(min_role))
        return TripMembership(trip_id=trip_id, sub=sub, role=role)

    def by_hash(
        self, _s: object, token_hash: str, now: datetime
    ) -> inv_db.Found | None:
        row = self.invitations.get(token_hash)
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        return inv_db.Found(row, "Kraków", "PL")

    def consume(self, _s: object, invitation_id: uuid.UUID, now: datetime) -> bool:
        row = self.one(_s, TRIP, invitation_id)
        if row is None or row.uses >= row.max_uses or row.revoked_at is not None:
            return False
        if row.expires_at <= now:
            return False
        row.uses += 1
        return True

    def insert_member(
        self, _s: object, _t: uuid.UUID, sub: str, role: TripRole
    ) -> None:
        self.roles[sub] = role

    def insert_invitation(self, _s: object, row: TripInvitation) -> TripInvitation:
        row.id = uuid.uuid4()
        row.created_at = datetime.now(UTC)
        row.uses = 0
        self.invitations[row.token_hash] = row
        return row

    def insert_profile(self, _s: object, profile: Profile) -> Profile:
        profile.id = uuid.uuid4()
        self.profiles.append(profile)
        return profile

    def account_profile(self, _s: object, _t: uuid.UUID, sub: str) -> uuid.UUID | None:
        return next((p.id for p in self.profiles if p.user_sub == sub), None)

    def one(
        self, _s: object, _t: uuid.UUID, invitation_id: uuid.UUID
    ) -> TripInvitation | None:
        return next(
            (r for r in self.invitations.values() if r.id == invitation_id), None
        )

    def listing(self, _s: object, _t: uuid.UUID) -> list[TripInvitation]:
        return list(self.invitations.values())

    def usable(self, _s: object, _t: uuid.UUID, now: datetime) -> int:
        return sum(
            r.uses < r.max_uses and r.revoked_at is None and r.expires_at > now
            for r in self.invitations.values()
        )

    def person(self, name: str, age: int = 70, sub: str | None = None) -> Profile:
        """A profile on the trip, with or without an account."""
        profile = Profile(
            id=uuid.uuid4(), trip_id=TRIP, display_name=name, age=age, user_sub=sub
        )
        self.profiles.append(profile)
        return profile

    def claimable(self, _s: object, _t: uuid.UUID) -> list[Profile]:
        return [p for p in self.profiles if p.user_sub is None]

    def profile(
        self, _s: object, trip_id: uuid.UUID, profile_id: uuid.UUID
    ) -> Profile | None:
        return next(
            (p for p in self.profiles if p.id == profile_id and p.trip_id == trip_id),
            None,
        )

    def link(
        self, _s: object, trip_id: uuid.UUID, profile_id: uuid.UUID, sub: str
    ) -> bool:
        profile = self.profile(_s, trip_id, profile_id)
        if profile is None or profile.user_sub is not None:
            return False
        profile.user_sub = sub
        return True

    def role_of(self, _s: object, _t: uuid.UUID, sub: str) -> TripRole | None:
        return self.roles.get(sub)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World(roles={HOST.sub: TripRole.HOST})
    w.profiles.append(Profile(id=uuid.uuid4(), trip_id=TRIP, user_sub=HOST.sub))
    for module, name, fake in (
        (trip_service, "get_membership", w.membership),
        (inv_db, "select_by_hash", w.by_hash),
        (inv_db, "consume_use", w.consume),
        (inv_db, "insert_invitation", w.insert_invitation),
        (inv_db, "select_one", w.one),
        (inv_db, "select_for_trip", w.listing),
        (inv_db, "count_usable", w.usable),
        (trips_db, "insert_member", w.insert_member),
        (trips_db, "select_member_role", w.role_of),
        (profiles_db, "insert_profile", w.insert_profile),
        (profiles_db, "select_account_profile_id", w.account_profile),
        (profiles_db, "select_claimable", w.claimable),
        (profiles_db, "select_profile", w.profile),
        (profiles_db, "link_account", w.link),
    ):
        monkeypatch.setattr(module, name, AsyncMock(side_effect=fake))
    return w


@pytest.fixture
def session(world: World) -> AsyncMock:
    mock = AsyncMock()

    def commit() -> None:
        world.commits += 1

    mock.commit = AsyncMock(side_effect=commit)
    mock.rollback = AsyncMock(side_effect=lambda: world.undo(GUEST.sub))
    return mock


def _client(session: AsyncMock, user: AuthenticatedUser) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    authorize(app, user)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client


@pytest.fixture
def host(session: AsyncMock) -> Iterator[TestClient]:
    yield from _client(session, HOST)


@pytest.fixture
def guest(session: AsyncMock) -> Iterator[TestClient]:
    yield from _client(session, GUEST)


ACCEPT = "accept_invitation"
BODY = {"token": TOKEN}


def test_host_creates_an_invitation_and_the_token_is_not_stored(
    host: TestClient, world: World
) -> None:
    response = host.post(path("create_invitation", trip_id=TRIP), json={})
    assert response.status_code == 201
    body = response.json()
    (row,) = world.invitations.values()
    assert row.token_hash == hash_token(body["token"])
    assert body["token"] not in {row.token_hash, str(row.id)}
    assert (row.max_uses, body["uses"]) == (10, 0)
    assert response.headers["cache-control"] == "no-store"
    assert "token" not in host.get(path("list_invitations", trip_id=TRIP)).json()[0]


def test_a_plain_member_cannot_manage_invitations(
    host: TestClient, world: World
) -> None:
    world.roles[HOST.sub] = TripRole.MEMBER
    row = world.invite()
    assert (
        host.post(path("create_invitation", trip_id=TRIP), json={}).status_code == 403
    )
    assert host.get(path("list_invitations", trip_id=TRIP)).status_code == 403
    url = path("revoke_invitation", trip_id=TRIP, invitation_id=row.id)
    assert host.delete(url).status_code == 403


@pytest.mark.parametrize(
    "payload", [{"max_uses": 0}, {"max_uses": 101}, {"expires_in_days": 31}]
)
def test_limits_are_validated(host: TestClient, payload: dict[str, int]) -> None:
    response = host.post(path("create_invitation", trip_id=TRIP), json=payload)
    assert response.status_code == 422


def test_the_number_of_working_invitations_is_capped(
    host: TestClient, world: World
) -> None:
    for i in range(20):
        world.invite(token_hash=f"{i:064}")
    response = host.post(path("create_invitation", trip_id=TRIP), json={})
    assert response.status_code == 409


def test_joining_creates_the_membership_and_a_profile_with_the_account(
    guest: TestClient, world: World
) -> None:
    row = world.invite()
    response = guest.post(path(ACCEPT), json={**BODY, "display_name": "  Ola "})
    assert response.status_code == 200
    body = response.json()
    assert (body["role"], body["already_member"]) == ("member", False)
    assert world.roles[GUEST.sub] is TripRole.MEMBER
    (profile,) = [p for p in world.profiles if p.user_sub == GUEST.sub]
    assert (profile.display_name, profile.age) == ("Ola", 35)
    assert body["profile_id"] == str(profile.id)
    assert row.uses == 1
    assert response.headers["cache-control"] == "no-store"


def test_a_placeholder_name_is_used_without_one(
    guest: TestClient, world: World
) -> None:
    world.invite()
    assert guest.post(path(ACCEPT), json=BODY).status_code == 200
    profile = next(p for p in world.profiles if p.user_sub == GUEST.sub)
    assert profile.display_name == "Uczestnik"


def test_accepting_twice_is_idempotent(guest: TestClient, world: World) -> None:
    row = world.invite()
    first = guest.post(path(ACCEPT), json=BODY).json()
    second = guest.post(path(ACCEPT), json=BODY)
    assert second.status_code == 200
    assert second.json() == first | {"already_member": True}
    assert len([p for p in world.profiles if p.user_sub == GUEST.sub]) == 1
    assert row.uses == 1


def test_a_removed_member_joins_again_with_a_new_profile(
    guest: TestClient, world: World
) -> None:
    world.invite()
    old = Profile(id=uuid.uuid4(), trip_id=TRIP, user_sub=None, display_name="Ola")
    world.profiles.append(old)  # kept without an account after the removal
    body = guest.post(path(ACCEPT), json=BODY).json()
    assert body["profile_id"] != str(old.id)
    assert old.user_sub is None
    assert world.roles[GUEST.sub] is TripRole.MEMBER


def test_a_member_may_reuse_a_used_up_link_but_a_stranger_may_not(
    guest: TestClient, world: World
) -> None:
    world.invite(max_uses=1, uses=1)
    assert guest.post(path(ACCEPT), json=BODY).status_code == 404
    assert GUEST.sub not in world.roles
    world.roles[GUEST.sub] = TripRole.CO_HOST
    again = guest.post(path(ACCEPT), json=BODY)
    assert again.status_code == 200
    assert again.json()["role"] == "co_host"


def test_the_use_limit_stops_the_next_person(guest: TestClient, world: World) -> None:
    row = world.invite(max_uses=1)
    assert guest.post(path(ACCEPT), json=BODY).status_code == 200
    world.roles.pop(GUEST.sub)  # removed again
    assert guest.post(path(ACCEPT), json=BODY).status_code == 404
    assert row.uses == 1


@pytest.mark.parametrize(
    "state",
    [
        {"revoked_at": datetime.now(UTC)},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
    ],
)
def test_revoked_and_expired_look_like_unknown(
    guest: TestClient, world: World, state: dict[str, object]
) -> None:
    world.invite(**state)
    unknown = guest.post(path(ACCEPT), json={"token": "x" * 43})
    dead = guest.post(path(ACCEPT), json=BODY)
    assert (dead.status_code, dead.json()) == (unknown.status_code, unknown.json())
    assert dead.status_code == 404
    assert dead.headers["cache-control"] == "no-store"
    assert GUEST.sub not in world.roles
    assert guest.post(path("preview_invitation"), json=BODY).status_code == 404


@pytest.mark.usefixtures("world")
def test_a_too_long_token_is_404_without_a_lookup(guest: TestClient) -> None:
    response = guest.post(path(ACCEPT), json={"token": "t" * 500})
    assert response.status_code == 404
    inv_db.select_by_hash.assert_not_called()  # ty: ignore[unresolved-attribute]


def test_preview_shows_the_trip(guest: TestClient, world: World) -> None:
    world.invite()
    response = guest.post(path("preview_invitation"), json=BODY)
    assert response.json() == {
        "trip_name": "Kraków",
        "destination": "PL",
        "already_member": False,
        "claimable_profiles": [],
    }
    assert response.headers["cache-control"] == "no-store"
    assert GUEST.sub not in world.roles


def test_revoking_is_idempotent_and_blocks_joining(
    host: TestClient, guest: TestClient, world: World
) -> None:
    row = world.invite()
    url = path("revoke_invitation", trip_id=TRIP, invitation_id=row.id)
    assert host.delete(url).json()["revoked_at"] is not None
    revoked_at = row.revoked_at
    assert host.delete(url).status_code == 200
    assert row.revoked_at == revoked_at
    assert guest.post(path(ACCEPT), json=BODY).status_code == 404
    other = path("revoke_invitation", trip_id=TRIP, invitation_id=uuid.uuid4())
    assert host.delete(other).status_code == 404


def test_a_concurrent_double_accept_ends_as_a_single_join(
    guest: TestClient, world: World, session: AsyncMock
) -> None:
    world.invite()

    def lose_the_race() -> None:
        world.roles[GUEST.sub] = TripRole.MEMBER  # the other request won
        world.profiles.append(
            Profile(id=uuid.uuid4(), trip_id=TRIP, user_sub=GUEST.sub)
        )
        cause = Exception("uq_profiles_trip_id")
        statement = "INSERT INTO profiles"
        raise IntegrityError(statement, {}, cause)

    session.commit.side_effect = lose_the_race
    session.rollback = AsyncMock()
    response = guest.post(path(ACCEPT), json=BODY)
    assert response.status_code == 200
    assert response.json()["already_member"] is True
    session.rollback.assert_awaited_once()


def test_a_member_without_a_profile_gets_one_and_no_use_is_taken(
    guest: TestClient, world: World
) -> None:
    row = world.invite()
    world.roles[GUEST.sub] = TripRole.MEMBER  # joined before profiles were created
    body = guest.post(path(ACCEPT), json=BODY).json()
    assert body["already_member"] is True
    assert [p.id for p in world.profiles if p.user_sub == GUEST.sub] == [
        uuid.UUID(body["profile_id"])
    ]
    assert row.uses == 0


def test_a_conflict_that_is_not_the_race_is_a_404_and_creates_nothing(
    guest: TestClient, world: World, session: AsyncMock
) -> None:
    world.invite()
    cause = Exception("uq_something_else")
    statement = "INSERT INTO trip_members"
    session.commit.side_effect = IntegrityError(statement, {}, cause)
    session.rollback = AsyncMock(side_effect=lambda: world.undo(GUEST.sub))
    response = guest.post(path(ACCEPT), json=BODY)
    assert response.status_code == 404
    assert GUEST.sub not in world.roles
    assert response.headers["cache-control"] == "no-store"
    session.rollback.assert_awaited_once()


def test_an_account_with_a_profile_but_no_membership_keeps_that_profile(
    guest: TestClient, world: World
) -> None:
    row = world.invite()
    profile = Profile(id=uuid.uuid4(), trip_id=TRIP, user_sub=GUEST.sub)
    world.profiles.append(profile)
    body = guest.post(path(ACCEPT), json=BODY).json()
    assert body["profile_id"] == str(profile.id)
    assert body["already_member"] is False
    assert world.roles[GUEST.sub] is TripRole.MEMBER
    assert len([p for p in world.profiles if p.user_sub == GUEST.sub]) == 1
    assert row.uses == 1


def test_no_free_use_creates_no_member_and_no_profile(
    guest: TestClient, world: World
) -> None:
    world.invite()
    profiles = len(world.profiles)
    inv_db.consume_use.side_effect = lambda *_: False  # ty: ignore[unresolved-attribute]
    response = guest.post(path(ACCEPT), json=BODY)
    assert response.status_code == 404
    assert GUEST.sub not in world.roles
    assert len(world.profiles) == profiles
    assert world.commits == 0


def test_the_use_is_taken_by_one_conditional_update() -> None:
    sql = str(
        inv_db.consume_stmt(uuid.uuid4(), datetime.now(UTC)).compile(
            dialect=postgresql.dialect()
        )
    )
    for part in (
        "trip_invitations.uses < trip_invitations.max_uses",
        "trip_invitations.revoked_at IS NULL",
        "trip_invitations.expires_at >",
        "SET uses=(trip_invitations.uses + ",
        "RETURNING trip_invitations.id",
    ):
        assert part in sql


def _claimable(client: TestClient) -> list[dict[str, str]]:
    body = client.post(path("preview_invitation"), json=BODY).json()
    return cast("list[dict[str, str]]", body["claimable_profiles"])


def test_preview_lists_only_name_and_age_group_of_profiles_without_an_account(
    guest: TestClient, world: World
) -> None:
    world.invite()
    granny = world.person("Babcia", age=72)
    world.person("Ala", sub="auth0|ala")  # has an account: not offered
    assert _claimable(guest) == [
        {"profile_id": str(granny.id), "display_name": "Babcia", "age_group": "senior"}
    ]


def test_preview_offers_nothing_to_someone_already_on_the_trip(
    guest: TestClient, world: World
) -> None:
    world.invite()
    world.person("Babcia")
    world.roles[GUEST.sub] = TripRole.MEMBER
    assert _claimable(guest) == []


def test_taking_over_a_profile_links_the_account_and_creates_no_new_one(
    guest: TestClient, world: World
) -> None:
    row = world.invite()
    granny = world.person("Babcia")
    before = len(world.profiles)
    response = guest.post(path(ACCEPT), json=BODY | {"profile_id": str(granny.id)})
    assert response.status_code == 200
    assert response.json()["profile_id"] == str(granny.id)
    assert response.json()["profile_claimed"] is True
    assert granny.user_sub == GUEST.sub
    assert len(world.profiles) == before
    assert world.roles[GUEST.sub] is TripRole.MEMBER
    assert (row.uses, world.commits) == (1, 1)
    assert response.headers["cache-control"] == "no-store"


def test_a_profile_that_has_an_account_is_a_409_and_nothing_changes(
    guest: TestClient, world: World
) -> None:
    world.invite()
    taken = world.person("Ala", sub="auth0|ala")
    response = guest.post(path(ACCEPT), json=BODY | {"profile_id": str(taken.id)})
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert taken.user_sub == "auth0|ala"
    assert GUEST.sub not in world.roles
    assert world.commits == 0


def test_a_profile_of_another_trip_is_a_404(guest: TestClient, world: World) -> None:
    world.invite()
    foreign = Profile(
        id=uuid.uuid4(), trip_id=uuid.uuid4(), display_name="Obca", age=30
    )
    world.profiles.append(foreign)
    response = guest.post(path(ACCEPT), json=BODY | {"profile_id": str(foreign.id)})
    assert response.status_code == 404
    assert foreign.user_sub is None
    assert GUEST.sub not in world.roles
    assert response.headers["cache-control"] == "no-store"


def test_two_accounts_racing_for_one_profile_one_wins_and_one_gets_409(
    session: AsyncMock, world: World
) -> None:
    world.invite()
    granny = world.person("Babcia")
    other = AuthenticatedUser(sub="auth0|other")
    statuses = [
        client.post(
            path(ACCEPT), json=BODY | {"profile_id": str(granny.id)}
        ).status_code
        for user in (GUEST, other)
        for client in _client(session, user)
    ]
    assert statuses == [200, 409]
    assert granny.user_sub == GUEST.sub


def test_the_claim_is_one_conditional_update() -> None:
    session = AsyncMock()
    session.execute.return_value = MagicMock()
    asyncio.run(real_link_account(session, TRIP, uuid.uuid4(), GUEST.sub))
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    for part in (
        "UPDATE profiles SET user_sub=",
        "profiles.user_sub IS NULL",
        "profiles.trip_id =",
        "RETURNING profiles.id",
    ):
        assert part in sql


def test_a_named_invitation_hands_over_its_profile_without_a_choice(
    guest: TestClient, world: World
) -> None:
    granny = world.person("Babcia")
    world.person("Dziadek")
    world.invite(profile_id=granny.id, max_uses=1)
    assert [p["profile_id"] for p in _claimable(guest)] == [str(granny.id)]
    body = guest.post(path(ACCEPT), json=BODY).json()
    assert (body["profile_id"], body["profile_claimed"]) == (str(granny.id), True)
    assert granny.user_sub == GUEST.sub


def test_a_named_invitation_refuses_a_different_profile(
    guest: TestClient, world: World
) -> None:
    granny = world.person("Babcia")
    grandpa = world.person("Dziadek")
    world.invite(profile_id=granny.id)
    response = guest.post(path(ACCEPT), json=BODY | {"profile_id": str(grandpa.id)})
    assert response.status_code == 409
    assert grandpa.user_sub is None
    assert GUEST.sub not in world.roles


def test_a_host_creates_a_named_invitation_that_works_once(
    host: TestClient, world: World
) -> None:
    granny = world.person("Babcia")
    response = host.post(
        path("create_invitation", trip_id=TRIP),
        json={"profile_id": str(granny.id)},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["profile_id"], body["max_uses"]) == (str(granny.id), 1)
    too_many = host.post(
        path("create_invitation", trip_id=TRIP),
        json={"profile_id": str(granny.id), "max_uses": 50},
    )
    assert too_many.status_code == 422


@pytest.mark.parametrize(("make", "expected"), [("account", 409), ("foreign", 404)])
def test_a_named_invitation_needs_a_free_profile_of_this_trip(
    host: TestClient, world: World, make: str, expected: int
) -> None:
    if make == "account":
        profile = world.person("Ala", sub="auth0|ala")
    else:
        profile = Profile(id=uuid.uuid4(), trip_id=uuid.uuid4(), display_name="X")
        world.profiles.append(profile)
    response = host.post(
        path("create_invitation", trip_id=TRIP), json={"profile_id": str(profile.id)}
    )
    assert response.status_code == expected
    assert not world.invitations


def test_a_removed_members_profile_can_be_taken_over(
    guest: TestClient, world: World
) -> None:
    world.invite()
    left = world.person("Dawny uczestnik")  # unlink_account left user_sub empty
    assert _claimable(guest)[0]["profile_id"] == str(left.id)


def test_a_dead_token_preview_is_404_with_no_store(guest: TestClient) -> None:
    response = guest.post(path("preview_invitation"), json=BODY)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_a_member_with_a_profile_is_not_blocked_by_a_named_mismatch(
    guest: TestClient, world: World
) -> None:
    granny = world.person("Babcia")
    other = world.person("Dziadek")
    world.invite(profile_id=granny.id)
    mine = world.person("Ja", sub=GUEST.sub)
    world.roles[GUEST.sub] = TripRole.MEMBER
    response = guest.post(path(ACCEPT), json=BODY | {"profile_id": str(other.id)})
    assert response.status_code == 200
    assert response.json()["profile_id"] == str(mine.id)
    assert other.user_sub is None
