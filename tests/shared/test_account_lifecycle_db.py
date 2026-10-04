"""Block check and account erasure against a real Postgres (``-m integration``)."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import tuttitrip.main  # ruff: ignore[unused-import]  # registers the erasers
from tuttitrip.profiles.models import Profile
from tuttitrip.shared.admin_users.services import erasure
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import database_url
from tuttitrip.shared.permissions.models import AccessToken, PermissionAudit
from tuttitrip.shared.permissions.registry import Access
from tuttitrip.shared.permissions.schemas import TokenScope
from tuttitrip.shared.permissions.services import permission_service
from tuttitrip.trips import db as trips_db
from tuttitrip.trips.invitations.models import TripInvitation
from tuttitrip.trips.models import Trip, TripMember
from tuttitrip.trips.schemas import TripCreate, TripRole
from tuttitrip.trips.services import trip_service

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _session() -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(database_url(get_settings().database))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


def _sub(label: str) -> str:
    return f"auth0|{label}-{uuid.uuid4().hex[:8]}"


async def _cleanup(session: AsyncSession, subs: list[str]) -> None:
    await session.rollback()
    for sub in subs:
        await trips_db.delete_trips_owned_by(session, sub)
        await permission_service.unblock_account(session, "test", sub)
    await session.commit()


def test_block_rides_in_the_grants_query_and_can_be_lifted() -> None:
    async def run() -> None:
        sub = _sub("blocked")
        async with _session() as session:
            try:
                _, blocked = await permission_service.load_access(session, sub)
                assert blocked is False
                await permission_service.block_account(session, "auth0|admin", sub)
                grants, blocked = await permission_service.load_access(session, sub)
                assert blocked is True
                assert grants  # the default role still resolves
                assert all(isinstance(g.level, Access) for g in grants)
                await permission_service.unblock_account(session, "auth0|admin", sub)
                assert (await permission_service.load_access(session, sub))[1] is False
            finally:
                await _cleanup(session, [sub])

    asyncio.run(run())


def test_deleting_a_host_hands_the_trip_to_the_first_co_host() -> None:
    async def run() -> None:
        host, co_host = _sub("host"), _sub("cohost")
        async with _session() as session:
            try:
                trip = await trip_service.create_trip(
                    session, host, TripCreate(name="T")
                )
                kept = await trip_service.create_trip(
                    session, host, TripCreate(name="U")
                )
                await trips_db.insert_member(
                    session, trip.id, co_host, TripRole.CO_HOST
                )
                await session.commit()

                await trip_service.erase_account(session, host)
                await permission_service.block_account(
                    session, "auth0|admin", host, deleted=True
                )

                owner = await session.scalar(
                    select(Trip.owner_sub).where(Trip.id == trip.id)
                )
                assert owner == co_host
                members = await trips_db.select_members(session, trip.id)
                roles = {sub: role for sub, (role, _) in members.items()}
                assert roles == {co_host: TripRole.HOST}
                assert await session.get(Trip, kept.id) is None  # no co-host: removed
                linked = await session.scalar(
                    select(Profile.id).where(Profile.user_sub == host)
                )
                assert linked is None
                assert (await permission_service.load_access(session, host))[1] is True
            finally:
                await _cleanup(session, [host, co_host])

    asyncio.run(run())


def test_deleting_a_host_without_co_host_hands_the_trip_to_the_first_member() -> None:
    async def run() -> None:
        host, early, late = _sub("host"), _sub("early"), _sub("late")
        async with _session() as session:
            try:
                trip = await trip_service.create_trip(
                    session, host, TripCreate(name="T")
                )
                await trips_db.insert_member(session, trip.id, early, TripRole.MEMBER)
                await trips_db.insert_member(session, trip.id, late, TripRole.MEMBER)
                await session.commit()

                await trip_service.erase_account(session, host)

                owner = await session.scalar(
                    select(Trip.owner_sub).where(Trip.id == trip.id)
                )
                assert owner == early
                members = await trips_db.select_members(session, trip.id)
                roles = {sub: role for sub, (role, _) in members.items()}
                assert roles == {early: TripRole.HOST, late: TripRole.MEMBER}
            finally:
                await _cleanup(session, [host, early, late])

    asyncio.run(run())


def test_registered_trip_eraser_is_the_one_in_main() -> None:

    assert trip_service.erase_account in erasure._ERASERS  # ruff: ignore[private-member-access]


def test_members_rows_of_a_deleted_account_are_gone() -> None:
    async def run() -> None:
        host, member = _sub("host"), _sub("member")
        async with _session() as session:
            try:
                trip = await trip_service.create_trip(
                    session, host, TripCreate(name="T")
                )
                await trips_db.insert_member(session, trip.id, member, TripRole.MEMBER)
                await session.commit()
                await trip_service.erase_account(session, member)
                await session.commit()
                rows = await session.scalars(
                    select(TripMember.user_sub).where(TripMember.user_sub == member)
                )
                assert rows.all() == []
            finally:
                await _cleanup(session, [host, member])

    asyncio.run(run())


def test_deleting_an_account_revokes_its_tokens_and_invitations() -> None:
    async def run() -> None:
        host, other = _sub("host"), _sub("other")
        async with _session() as session:
            try:
                trip = await trip_service.create_trip(
                    session, other, TripCreate(name="T")
                )
                profile_id = await session.scalar(
                    select(Profile.id).where(Profile.trip_id == trip.id)
                )
                assert profile_id is not None
                await trips_db.insert_member(session, trip.id, host, TripRole.CO_HOST)
                soon = datetime.now(UTC) + timedelta(days=1)
                token = AccessToken(
                    token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    scope=next(iter(TokenScope)),
                    trip_id=trip.id,
                    profile_id=profile_id,
                    expires_at=soon,
                    created_by=host,
                )
                invitation = TripInvitation(
                    trip_id=trip.id,
                    token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    created_by_sub=host,
                    expires_at=soon,
                    max_uses=3,
                )
                session.add_all([token, invitation])
                await session.commit()

                counts = await erasure.erase(session, host)
                counts |= await permission_service.revoke_issued_tokens(session, host)
                await permission_service.block_account(
                    session, "auth0|admin", host, deleted=True, change=counts
                )

                await session.refresh(token)
                await session.refresh(invitation)
                assert token.revoked_at is not None
                assert invitation.revoked_at is not None
                assert counts["invitations_revoked"] == 1
                assert counts["access_tokens_revoked"] == 1
                assert counts["memberships_removed"] == 1
                audit = await session.scalar(
                    select(PermissionAudit.change)
                    .where(
                        PermissionAudit.target_sub == host,
                        PermissionAudit.action == "user.delete",
                    )
                    .order_by(PermissionAudit.id.desc())
                    .limit(1)
                )
                assert audit == counts
            finally:
                await _cleanup(session, [host, other])

    asyncio.run(run())
