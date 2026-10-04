"""City suggestions: catalog first, Photon second, a dead geocoder is no error."""

import asyncio
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from tests.shared.paths import path
from tests.shared.tokens import bearer, make_verifier
from tuttitrip.main import create_app
from tuttitrip.places import db
from tuttitrip.places.candidates.logic.slug import slugify
from tuttitrip.places.cities.api import get_geocoder
from tuttitrip.places.cities.logic.suggest import GeocoderHit, parse_photon
from tuttitrip.places.cities.schemas import CityLang
from tuttitrip.places.cities.services.photon import PhotonGeocoder
from tuttitrip.places.models import City
from tuttitrip.shared.auth.api import get_token_verifier
from tuttitrip.shared.config.settings import GeocoderSettings
from tuttitrip.shared.db.api import get_session
from tuttitrip.shared.permissions.api import get_user_grants
from tuttitrip.shared.permissions.logic.resolution import Grant
from tuttitrip.shared.permissions.registry import Access, Feature


def feature(
    name: str, code: str, country: str, state: str | None = None
) -> dict[str, Any]:
    props = {"name": name, "countrycode": code, "country": country, "type": "city"}
    if state:
        props["state"] = state
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [18.65, 54.35]},
    }


PHOTON_PL = {
    "features": [
        feature("Gdansk", "PL", "Polska", "Województwo pomorskie"),
        feature("Gdańsk", "PL", "Polska"),  # a duplicate of the catalog city
        feature("Lisboa", "PT", "Portugal", "Lisboa"),
        {"properties": {"name": "no country"}, "geometry": {}},
    ]
}


def city(slug: str, name: str, country: str = "PL") -> City:
    return City(
        slug=slug,
        name=name,
        country=country,
        timezone="Europe/Warsaw",
        currency="PLN",
        center_lat=50.0,
        center_lon=19.9,
        bbox_south=0,
        bbox_west=0,
        bbox_north=0,
        bbox_east=0,
    )


class Upstream:
    """A Photon stand-in that records what it was asked."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def photon(
    upstream: Upstream,
    clock: Callable[[], float] = lambda: 0.0,
    settings: GeocoderSettings | None = None,
) -> PhotonGeocoder:
    return PhotonGeocoder(
        settings or GeocoderSettings(), httpx.MockTransport(upstream), clock
    )


def run(
    geocoder: PhotonGeocoder, query: str, lang: CityLang
) -> list[GeocoderHit] | None:
    return asyncio.run(geocoder.search(query, lang))


def ok(body: dict[str, Any] | None = None) -> Upstream:
    return Upstream(lambda _: httpx.Response(200, json=body or PHOTON_PL))


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    application = create_app()
    verifier = make_verifier()
    application.dependency_overrides[get_token_verifier] = lambda: verifier
    application.dependency_overrides[get_session] = lambda: None
    application.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.PLACES_CATALOG, Access.READ)
    ]
    application.dependency_overrides[get_geocoder] = lambda: photon(ok())
    catalog = [city("gdansk", "Gdańsk"), city("krakow", "Kraków")]
    monkeypatch.setattr(db, "select_cities", AsyncMock(return_value=catalog))
    monkeypatch.setattr(
        db, "select_city_slugs_with_places", AsyncMock(return_value={"krakow"})
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def search(client: TestClient, **params: str | int) -> Response:
    return client.get(path("search_cities"), params=params, headers=bearer())


def test_catalog_cities_come_first_with_the_ready_marker(client: TestClient) -> None:
    body = search(client, q="gda").json()
    assert body["geocoder_available"] is True
    assert [(s["slug"], s["source"]) for s in body["items"]][:1] == [
        ("gdansk", "catalog")
    ]
    ready = {s["slug"]: s["catalog_ready"] for s in body["items"]}
    assert ready["gdansk"] is False  # in the catalog, no places yet


def test_ready_flag_and_order_for_a_prefix(client: TestClient) -> None:
    items = search(client, q="kra").json()["items"]
    assert items[0]["slug"] == "krakow"
    assert items[0]["catalog_ready"] is True
    assert items[0]["city_query"] is None


def test_geocoder_city_has_a_query_that_slugifies_to_its_slug(
    client: TestClient,
) -> None:
    items = search(client, q="lis").json()["items"]
    lisbon = next(s for s in items if s["name"] == "Lisboa")
    assert lisbon["source"] == "geocoder"
    assert lisbon["catalog_ready"] is False
    assert lisbon["country"] == "PT"
    assert lisbon["city_query"] == "Lisboa, Portugal"
    assert slugify(lisbon["city_query"]) == lisbon["slug"] == "lisboa-portugal"


def test_geocoder_duplicates_of_catalog_cities_are_dropped(client: TestClient) -> None:
    items = search(client, q="gda").json()["items"]
    assert [s["name"] for s in items].count("Gdańsk") == 1
    assert "Gdansk" not in [s["name"] for s in items]
    assert {s["name"] for s in items} == {"Gdańsk", "Lisboa"}


def test_paging_slices_the_merged_list(client: TestClient) -> None:
    page = search(client, q="gda", size=1, page=2).json()
    assert (page["total"], page["pages"], page["size"]) == (2, 2, 1)
    assert [s["name"] for s in page["items"]] == ["Lisboa"]


@pytest.mark.parametrize(
    "params", [{"q": "a"}, {"q": "kra", "size": 21}, {"q": "kra", "lang": "xx"}, {}]
)
def test_bad_query_is_422(client: TestClient, params: dict[str, str | int]) -> None:
    assert search(client, **params).status_code == 422


def test_needs_a_token_and_the_permission(app: FastAPI) -> None:
    client = TestClient(app)
    assert client.get(path("search_cities"), params={"q": "kra"}).status_code == 401
    app.dependency_overrides[get_user_grants] = lambda: [
        Grant(Feature.TRIPS_CORE, Access.READ)
    ]
    response = client.get(path("search_cities"), params={"q": "kra"}, headers=bearer())
    assert response.status_code == 403


def test_a_dead_geocoder_still_answers_with_the_catalog(app: FastAPI) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        msg = "down"
        raise httpx.ConnectError(msg)

    app.dependency_overrides[get_geocoder] = lambda: photon(Upstream(boom))
    body = search(TestClient(app), q="kra").json()
    assert body["geocoder_available"] is False
    assert [s["slug"] for s in body["items"]] == ["krakow"]


def test_polish_uses_the_local_names_and_english_asks_for_en() -> None:
    upstream = ok()
    geocoder = photon(
        upstream, settings=GeocoderSettings(user_agent="TuttiTrip-test/1")
    )
    run(geocoder, "lis", CityLang.PL)
    run(geocoder, "lis", CityLang.EN)
    pl, en = (r.url.params for r in upstream.requests)
    assert "lang" not in pl
    assert en["lang"] == "en"
    assert pl["layer"] == "city"
    assert upstream.requests[0].headers["user-agent"] == "TuttiTrip-test/1"


def test_repeated_queries_hit_the_cache_until_it_expires() -> None:
    now = [0.0]
    upstream = ok()
    geocoder = photon(upstream, lambda: now[0], GeocoderSettings(cache_ttl_seconds=60))
    first = run(geocoder, "Lis", CityLang.PL)
    assert run(geocoder, "lis ", CityLang.PL) == first
    assert len(upstream.requests) == 1
    now[0] = 61.0
    run(geocoder, "lis", CityLang.PL)
    assert len(upstream.requests) == 2


def test_the_request_budget_is_per_minute() -> None:
    now = [0.0]
    upstream = ok()
    geocoder = photon(
        upstream, lambda: now[0], GeocoderSettings(max_requests_per_minute=2)
    )
    assert run(geocoder, "aa", CityLang.PL) is not None
    assert run(geocoder, "bb", CityLang.PL) is not None
    assert run(geocoder, "cc", CityLang.PL) is None
    assert len(upstream.requests) == 2
    now[0] = 61.0
    assert run(geocoder, "cc", CityLang.PL) is not None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json=[]),
    ],
)
def test_bad_upstream_answers_are_unavailable_not_cached(
    response: httpx.Response,
) -> None:
    upstream = Upstream(lambda _: response)
    geocoder = photon(upstream)
    assert run(geocoder, "lis", CityLang.PL) is None
    assert run(geocoder, "lis", CityLang.PL) is None
    assert len(upstream.requests) == 2


def test_parse_skips_features_without_a_name_or_country() -> None:
    names = [h.name for h in parse_photon(PHOTON_PL)]
    assert names == ["Gdansk", "Gdańsk", "Lisboa"]
    assert parse_photon({}) == []
