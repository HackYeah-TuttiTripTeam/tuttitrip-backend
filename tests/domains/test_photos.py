"""Photos: type and size rules, routes and the SQL on Postgres."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.shared.fakes import authorize
from tests.shared.paths import path
from tuttitrip.main import create_app
from tuttitrip.profiles.services import profile_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.db.session import database_url
from tuttitrip.trips.models import Trip
from tuttitrip.trips.photos import db as photos_db
from tuttitrip.trips.photos.logic.rules import (
    JPEG,
    PNG,
    WEBP,
    can_delete,
    problem,
    sniff_type,
)
from tuttitrip.trips.photos.models import TripPhoto
from tuttitrip.trips.photos.schemas import PhotoQuery
from tuttitrip.trips.schemas import TripMembership, TripRole
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripNotFoundError

HOST, CO_HOST, MEMBER = TripRole.HOST, TripRole.CO_HOST, TripRole.MEMBER
TRIP = uuid.uuid4()
ME = AuthenticatedUser(sub="auth0|me")
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"j" * 20
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"p" * 20
WEBP_BYTES = b"RIFF\x10\x00\x00\x00WEBP" + b"w" * 10


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG_BYTES, JPEG),
        (PNG_BYTES, PNG),
        (WEBP_BYTES, WEBP),
        (b"GIF89a" + b"x" * 20, None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
        (b"RIFF\x10\x00\x00\x00WAVE" + b"x", None),
        (b"%PDF-1.7", None),
        (b"", None),
    ],
)
def test_type_comes_from_the_bytes(data: bytes, expected: str | None) -> None:
    assert sniff_type(data) == expected


def test_problem_reports_empty_large_and_foreign_files() -> None:
    assert problem(JPEG_BYTES, 100, "image") is None
    assert problem(b"", 100, "image") == "The image is empty"
    assert problem(JPEG_BYTES, 10, "image") == "The image is larger than 10 bytes"
    assert problem(b"GIF89a....", 100, "thumbnail") == (
        "The thumbnail must be a JPEG, PNG or WebP image"
    )


@pytest.mark.parametrize(
    ("role", "mine", "allowed"),
    [
        (MEMBER, True, True),
        (MEMBER, False, False),
        (CO_HOST, False, False),
        (CO_HOST, True, True),
        (HOST, False, True),
    ],
)
def test_author_or_host_deletes(role: TripRole, mine: bool, allowed: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
    assert can_delete(role, "a", "a" if mine else "b") is allowed


class State:
    """In-memory trip behind the mocked service and db layers."""

    def __init__(self) -> None:
        self.roles: dict[str, TripRole] = {}
        self.photos: dict[uuid.UUID, TripPhoto] = {}

    def add_photo(self, author: str, trip_id: uuid.UUID = TRIP) -> TripPhoto:
        photo = TripPhoto(
            id=uuid.uuid4(),
            trip_id=trip_id,
            author_sub=author,
            content_type=JPEG,
            size_bytes=len(JPEG_BYTES),
            thumbnail_type=PNG,
            thumbnail=PNG_BYTES,
            image=JPEG_BYTES,
            created_at=datetime.now(UTC),
        )
        self.photos[photo.id] = photo
        return photo


@pytest.fixture
def state(monkeypatch: pytest.MonkeyPatch) -> State:
    trip = State()

    def membership(
        _s: object, trip_id: uuid.UUID, sub: str, min_role: TripRole
    ) -> TripMembership:
        role = trip.roles.get(sub)
        if role is None:
            raise TripNotFoundError(str(trip_id))
        if not role.satisfies(min_role):
            raise trip_service.TripRoleError(str(min_role))
        return TripMembership(trip_id=trip_id, sub=sub, role=role)

    def insert(_s: object, photo: TripPhoto) -> TripPhoto:
        photo.id = uuid.uuid4()
        photo.created_at = datetime.now(UTC)
        trip.photos[photo.id] = photo
        return photo

    def select_one(
        _s: object, trip_id: uuid.UUID, photo_id: uuid.UUID, *, with_image: bool
    ) -> TripPhoto | None:
        photo = trip.photos.get(photo_id)
        assert photo is None or photo.image or not with_image
        return photo if photo is not None and photo.trip_id == trip_id else None

    def select_many(_s: object, _t: uuid.UUID, _sub: str, query: PhotoQuery) -> object:
        rows = list(trip.photos.values())
        return SimpleNamespace(
            items=rows, total=len(rows), page=query.page, size=query.size, pages=1
        )

    monkeypatch.setattr(
        trip_service, "get_membership", AsyncMock(side_effect=membership)
    )
    monkeypatch.setattr(
        profile_service,
        "list_profiles",
        AsyncMock(
            return_value=[
                SimpleNamespace(user_sub=ME.sub, display_name="Ja"),
                SimpleNamespace(user_sub=None, display_name="Bez konta"),
            ]
        ),
    )
    monkeypatch.setattr(photos_db, "insert_photo", AsyncMock(side_effect=insert))
    monkeypatch.setattr(
        photos_db, "count_photos", AsyncMock(side_effect=lambda *_: len(trip.photos))
    )
    monkeypatch.setattr(photos_db, "select_photo", AsyncMock(side_effect=select_one))
    monkeypatch.setattr(photos_db, "select_photos", AsyncMock(side_effect=select_many))
    monkeypatch.setattr(
        photos_db,
        "delete_photo",
        AsyncMock(side_effect=lambda _s, _t, pid: trip.photos.pop(pid)),
    )
    return trip


@pytest.fixture
def session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(session: AsyncMock) -> Iterator[TestClient]:
    app: FastAPI = create_app()
    authorize(app, ME)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def _upload(
    client: TestClient, image: bytes = JPEG_BYTES, thumbnail: bytes = PNG_BYTES
) -> Response:
    return client.post(
        path("upload_photo", trip_id=TRIP),
        files={
            "image": ("../../etc/passwd.jpg", image, "image/jpeg"),
            "thumbnail": ("t.png", thumbnail, "image/png"),
        },
    )


def test_member_uploads_and_members_list_with_inline_thumbnail(
    client: TestClient, state: State, session: AsyncMock
) -> None:
    state.roles[ME.sub] = MEMBER
    response = _upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["is_mine"] is True
    assert body["author_name"] == "Ja"
    assert body["content_type"] == JPEG
    assert body["thumbnail"].startswith("data:image/png;base64,")
    assert "passwd" not in response.text
    session.commit.assert_awaited_once()
    stored = next(iter(state.photos.values()))
    assert stored.image == JPEG_BYTES
    listing = client.get(path("list_photos", trip_id=TRIP)).json()
    assert [p["id"] for p in listing["items"]] == [body["id"]]
    assert "image" not in listing["items"][0]
    assert "author_sub" not in listing["items"][0]


def test_type_is_taken_from_bytes_not_from_the_header(
    client: TestClient, state: State
) -> None:
    state.roles[ME.sub] = MEMBER
    assert _upload(client, image=b"GIF89a" + b"x" * 20).status_code == 422
    assert _upload(client, thumbnail=b"<svg/>").status_code == 422
    assert _upload(client, image=WEBP_BYTES).json()["content_type"] == WEBP
    assert len(state.photos) == 1


def test_oversize_and_empty_files_are_422(
    client: TestClient, state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    state.roles[ME.sub] = MEMBER
    monkeypatch.setattr(get_settings().photos, "max_image_bytes", 30)
    monkeypatch.setattr(get_settings().photos, "max_thumbnail_bytes", 30)
    too_big = JPEG_BYTES + b"x" * 50
    response = _upload(client, image=too_big)
    assert response.status_code == 422
    assert "larger than 30 bytes" in response.json()["detail"]
    assert _upload(client, thumbnail=too_big).status_code == 422
    assert _upload(client, image=b"").status_code == 422
    assert not state.photos


def test_photo_limit_per_trip_is_422(
    client: TestClient, state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    state.roles[ME.sub] = MEMBER
    monkeypatch.setattr(get_settings().photos, "max_per_trip", 1)
    assert _upload(client).status_code == 201
    assert _upload(client).status_code == 422


def test_outsider_gets_404_everywhere(client: TestClient, state: State) -> None:
    photo = state.add_photo("auth0|ola")
    assert _upload(client).status_code == 404
    assert client.get(path("list_photos", trip_id=TRIP)).status_code == 404
    image = path("get_photo_image", trip_id=TRIP, photo_id=photo.id)
    assert client.get(image).status_code == 404
    delete = client.delete(path("delete_photo", trip_id=TRIP, photo_id=photo.id))
    assert delete.status_code == 404
    assert photo.id in state.photos


def test_member_downloads_the_image_with_safe_headers(
    client: TestClient, state: State
) -> None:
    state.roles[ME.sub] = MEMBER
    photo = state.add_photo("auth0|ola")
    response = client.get(path("get_photo_image", trip_id=TRIP, photo_id=photo.id))
    assert response.status_code == 200
    assert response.content == JPEG_BYTES
    assert response.headers["content-type"] == JPEG
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"].startswith("private")


def test_photo_of_another_trip_is_404(client: TestClient, state: State) -> None:
    state.roles[ME.sub] = MEMBER
    photo = state.add_photo("auth0|ola", trip_id=uuid.uuid4())
    response = client.get(path("get_photo_image", trip_id=TRIP, photo_id=photo.id))
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("role", "author", "expected"),
    [
        (MEMBER, ME.sub, 204),
        (MEMBER, "auth0|ola", 403),
        (CO_HOST, "auth0|ola", 403),
        (HOST, "auth0|ola", 204),
    ],
)
def test_delete_by_author_or_host(
    client: TestClient, state: State, role: TripRole, author: str, expected: int
) -> None:
    state.roles[ME.sub] = role
    photo = state.add_photo(author)
    response = client.delete(path("delete_photo", trip_id=TRIP, photo_id=photo.id))
    assert response.status_code == expected
    assert (photo.id in state.photos) is (expected == 403)


@pytest.mark.integration
def test_sql_list_defers_the_image_and_filters_by_author() -> None:
    """Runs against the local Postgres (`alembic upgrade head` done)."""

    async def run() -> None:
        engine = create_async_engine(database_url(get_settings().database))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            trip = Trip(owner_sub="auth0|it", name="Photos IT")
            session.add(trip)
            await session.flush()
            for author, size in (("a", 10), ("b", 99)):
                await photos_db.insert_photo(
                    session,
                    TripPhoto(
                        trip_id=trip.id,
                        author_sub=author,
                        content_type=JPEG,
                        size_bytes=size,
                        thumbnail_type=PNG,
                        thumbnail=PNG_BYTES,
                        image=JPEG_BYTES * size,
                    ),
                )
            await session.commit()
            session.expunge_all()
            page = await photos_db.select_photos(
                session, trip.id, "a", PhotoQuery(sort="size_bytes", dir="desc")
            )
            assert [p.size_bytes for p in page.items] == [99, 10]
            assert "image" in inspect(page.items[0]).unloaded
            mine = await photos_db.select_photos(
                session, trip.id, "a", PhotoQuery(mine=True)
            )
            assert [p.author_sub for p in mine.items] == ["a"]
            others = await photos_db.select_photos(
                session, trip.id, "a", PhotoQuery(mine=False)
            )
            assert [p.author_sub for p in others.items] == ["b"]
            assert await photos_db.count_photos(session, trip.id) == 2
            full = await photos_db.select_photo(
                session, trip.id, page.items[1].id, with_image=True
            )
            assert full is not None
            assert full.image == JPEG_BYTES * 10
            await session.delete(trip)
            await session.commit()
        await engine.dispose()

    asyncio.run(run())
