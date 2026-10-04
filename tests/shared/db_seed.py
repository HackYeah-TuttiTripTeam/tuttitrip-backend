"""Fixtures of backend#38 written to a real PostgreSQL, and people added over HTTP.

For the integration tests (`pytest -m integration`): the city and places are
written once and removed at the end; the people go in through the public API.
"""

from decimal import Decimal

import httpx
from sqlalchemy import delete, select

from tests.fixtures.city import CHECKED_AT, CITY_SLUG, city, place_id, places
from tests.fixtures.personas import Persona
from tuttitrip.places.models import City, Place, PlacePrice, TransitFare
from tuttitrip.places.schemas import PlaceRead
from tuttitrip.shared.db.session import get_sessionmaker


def place_row(place: PlaceRead) -> Place:
    hours = place.hours
    return Place(
        id=place.id,
        city_slug=place.city_slug,
        name=place.name,
        category=place.category.value,
        tags=[t.value for t in place.tags],
        lat=place.lat,
        lon=place.lon,
        source_key=place.source_key,
        google_place_id=place.google_place_id,
        opening_hours=hours.opening_hours.model_dump(mode="json")
        if hours.opening_hours
        else None,
        hours_source_url=hours.source_url,
        hours_verified=hours.verified,
        hours_checked_at=hours.checked_at,
        typical_visit_min=place.typical_visit_min,
        segment_km=place.segment_km,
        transfer_min=place.transfer_min,
        queue_min=place.queue_min,
        stairs=place.stairs,
        wheelchair=place.wheelchair,
        indoor=place.indoor,
        iconic=place.iconic,
        cuisine=place.cuisine.value if place.cuisine else None,
        diet_tags=[t.value for t in place.diet_tags],
        amenities=[a.value for a in place.amenities],
        source=place.source.value,
    )


async def seed_city() -> None:
    fixture_city = city()
    async with get_sessionmaker()() as session:
        if await session.get(City, CITY_SLUG) is None:
            session.add(City(**fixture_city.model_dump()))
            await session.flush()
        for place in places().values():
            if await session.get(Place, place.id) is not None:
                continue
            session.add(place_row(place))
            await session.flush()
            session.add_all(
                PlacePrice(
                    place_id=place.id,
                    **price.model_dump(exclude={"age_min", "age_max"})
                    | {
                        "ticket_category": price.ticket_category.value,
                        "unit": price.unit.value,
                        "age_min": price.age_min,
                        "age_max": price.age_max,
                    },
                )
                for price in place.prices
            )
        if not (
            await session.execute(
                select(TransitFare).where(TransitFare.city_slug == CITY_SLUG)
            )
        ).first():
            session.add_all(
                TransitFare(
                    city_slug=CITY_SLUG,
                    ticket_type=ticket,
                    person_category=category,
                    amount=Decimal(amount),
                    currency="PLN",
                    source_url="https://example.test/taryfa",
                    verified=True,
                    checked_at=CHECKED_AT,
                )
                for ticket, category, amount in (
                    ("single", "adult", "4"),
                    ("24h", "adult", "15"),
                    ("single", "child", "2"),
                    ("24h", "child", "8"),
                )
            )
        await session.commit()


async def unseed_city() -> None:
    async with get_sessionmaker()() as session:
        # Prices and ratings go with the places (ON DELETE CASCADE).
        await session.execute(delete(Place).where(Place.city_slug == CITY_SLUG))
        await session.execute(
            delete(TransitFare).where(TransitFare.city_slug == CITY_SLUG)
        )
        await session.execute(delete(City).where(City.slug == CITY_SLUG))
        await session.commit()


async def add_person(http: httpx.AsyncClient, base: str, persona: Persona) -> str:
    made = await http.post(
        f"{base}/profiles",
        json=persona.profile.model_dump(mode="json", exclude_none=True),
    )
    assert made.status_code == 201, made.text
    profile_id = str(made.json()["id"])
    prefs = await http.put(
        f"{base}/profiles/{profile_id}/preferences",
        json=persona.preferences.model_dump(mode="json"),
    )
    assert prefs.status_code == 200, prefs.text
    for key, rating in persona.ratings.items():
        voted = await http.put(
            f"{base}/profiles/{profile_id}/ratings/{place_id(key)}",
            json=rating.model_dump(mode="json", exclude_none=True),
        )
        assert voted.status_code == 200, voted.text
    for key in persona.vetoes:
        veto = await http.post(
            f"{base}/vetoes",
            json={"profile_id": profile_id, "place_id": str(place_id(key))},
        )
        assert veto.status_code == 201, veto.text
    return profile_id
