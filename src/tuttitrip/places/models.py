"""Place catalog ORM models: cities, places, ticket prices and transit fares.

Provenance rule (CHECK constraints): a row may be ``verified`` only when it
names a source and the date it was checked. Prices: free admission is a row
with ``amount`` 0 and ``verified`` true; no row means the price is unknown.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tuttitrip.places.schemas import (
    Amenity,
    Cuisine,
    DietTag,
    OsmType,
    PlaceCategory,
    PlaceHours,
    PlaceSource,
    PlaceTag,
    PriceUnit,
    TicketCategory,
)
from tuttitrip.shared.db.base import Base

_PROVENANCE = "NOT verified OR (source_url IS NOT NULL AND checked_at IS NOT NULL)"
_CURRENCY = "currency ~ '^[A-Z]{3}$'"
_EMPTY_ARRAY = text("'{}'")


def _values(values: type[StrEnum]) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


def _in(column: str, values: type[StrEnum]) -> str:
    """SQL ``column IN (...)`` for the values of a string enum.

    Args:
        column: Column name.
        values: The enum class.

    Returns:
        A check expression (NULL passes, as for any CHECK).
    """
    return f"{column} IN ({_values(values)})"


def _subset(column: str, values: type[StrEnum]) -> str:
    """SQL ``column <@ ARRAY[...]`` for an array of enum values.

    Args:
        column: Array column name.
        values: The enum class.

    Returns:
        A check expression.
    """
    return f"{column} <@ ARRAY[{_values(values)}]::varchar[]"


class City(Base):
    """A city the planner covers; hours are in its local time zone."""

    __tablename__ = "cities"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9-]+$'", name="slug"),
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country"),
        CheckConstraint(_CURRENCY, name="currency"),
    )

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    timezone: Mapped[str] = mapped_column(String(64))  # IANA, e.g. Europe/London
    currency: Mapped[str] = mapped_column(String(3))  # ISO 4217
    center_lat: Mapped[float]
    center_lon: Mapped[float]
    bbox_south: Mapped[float]
    bbox_west: Mapped[float]
    bbox_north: Mapped[float]
    bbox_east: Mapped[float]


class CityFetch(Base):
    """When a city was last fetched from OpenStreetMap (written by the worker).

    The refresh period starts at ``fetched_at``; ``osm_relation_id`` saves a
    geocoding call on the next refresh.
    """

    __tablename__ = "city_fetches"

    city_slug: Mapped[str] = mapped_column(
        ForeignKey("cities.slug", ondelete="CASCADE"), primary_key=True
    )
    osm_relation_id: Mapped[int] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime]
    stored: Mapped[int]  # places written by that fetch


class CityFetchAttempt(Base):
    """One Overpass reservation of a worker workflow (the daily quota).

    Failed attempts count too, so a row is written before the call. No FK to
    ``cities``: the reservation can precede the city row.
    """

    __tablename__ = "city_fetch_attempts"

    workflow_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    city_slug: Mapped[str] = mapped_column(String(64))
    reserved_at: Mapped[datetime] = mapped_column(index=True)


class Place(Base):
    """A catalog place (attraction, restaurant, lodging...) with planning inputs."""

    __tablename__ = "places"
    __table_args__ = (
        # `osm_id` alone is not unique: a node and a way may share the number.
        UniqueConstraint("osm_type", "osm_id"),
        # Idempotent upsert key of the sheet import (sheet rows have no OSM id).
        UniqueConstraint("source", "source_key"),
        CheckConstraint(_in("category", PlaceCategory), name="category"),
        CheckConstraint(_in("source", PlaceSource), name="source"),
        CheckConstraint(_in("osm_type", OsmType), name="osm_type"),
        CheckConstraint(_in("cuisine", Cuisine), name="cuisine"),
        CheckConstraint(_subset("tags", PlaceTag), name="tags"),
        CheckConstraint(_subset("diet_tags", DietTag), name="diet_tags"),
        CheckConstraint(_subset("amenities", Amenity), name="amenities"),
        CheckConstraint("(osm_type IS NULL) = (osm_id IS NULL)", name="osm_ref"),
        CheckConstraint("stairs BETWEEN 0 AND 1", name="stairs"),
        CheckConstraint("lat BETWEEN -90 AND 90", name="lat"),
        CheckConstraint("lon BETWEEN -180 AND 180", name="lon"),
        CheckConstraint(
            "typical_visit_min >= 0 AND segment_km >= 0"
            " AND transfer_min >= 0 AND queue_min >= 0",
            name="non_negative",
        ),
        CheckConstraint(
            "NOT hours_verified OR (opening_hours IS NOT NULL"
            " AND hours_source_url IS NOT NULL AND hours_checked_at IS NOT NULL)",
            name="hours_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    city_slug: Mapped[str] = mapped_column(ForeignKey("cities.slug"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    # Street address as printed on a plan; NULL when the import has none.
    address: Mapped[str | None] = mapped_column(String(300))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=list, server_default=_EMPTY_ARRAY
    )
    # Coordinates come from OSM or the sheet, never from Google.
    lat: Mapped[float]
    lon: Mapped[float]
    osm_type: Mapped[str | None] = mapped_column(String(8))
    osm_id: Mapped[int | None] = mapped_column(BigInteger)
    source_key: Mapped[str | None] = mapped_column(String(200))
    # Kept without limit (place IDs are exempt from the cache restrictions).
    google_place_id: Mapped[str | None] = mapped_column(String(200))
    # Weekly format with closed dates (`OpeningHours`); times are city-local.
    opening_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    hours_source_url: Mapped[str | None] = mapped_column(Text)
    hours_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    hours_checked_at: Mapped[datetime | None]
    # The OSM import does not know the visit length: the default is an hour.
    typical_visit_min: Mapped[int] = mapped_column(
        default=60, server_default=text("60")
    )  # tau_p
    segment_km: Mapped[float] = mapped_column(default=0.0, server_default=text("0"))
    transfer_min: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    queue_min: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    stairs: Mapped[float | None]  # NULL = unknown, which is not "no stairs"
    wheelchair: Mapped[bool | None]
    indoor: Mapped[bool | None]  # NULL = unknown, which is not "outdoors"
    iconic: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    cuisine: Mapped[str | None] = mapped_column(String(32))
    diet_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=list, server_default=_EMPTY_ARRAY
    )
    # Lodging only (category `lodging`); checked by the requirements contract.
    amenities: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=list, server_default=_EMPTY_ARRAY
    )
    source: Mapped[str] = mapped_column(String(16))

    prices: Mapped[list[PlacePrice]] = relationship(
        back_populates="place", lazy="raise", order_by="PlacePrice.ticket_category"
    )

    @property
    def hours(self) -> PlaceHours:
        """Opening hours with their provenance (unverified unless sourced).

        Returns:
            The hours DTO.
        """
        return PlaceHours.model_validate(
            {
                "opening_hours": self.opening_hours,
                "source_url": self.hours_source_url,
                "verified": self.hours_verified,
                "checked_at": self.hours_checked_at,
            }
        )


class PlacePrice(Base):
    """A price with its source: a ticket, or lodging per night (`unit`)."""

    __tablename__ = "place_prices"
    __table_args__ = (
        UniqueConstraint("place_id", "ticket_category"),
        CheckConstraint(_in("ticket_category", TicketCategory), name="ticket_category"),
        CheckConstraint(_in("unit", PriceUnit), name="unit"),
        CheckConstraint("amount >= 0", name="amount"),
        CheckConstraint(_CURRENCY, name="currency"),
        CheckConstraint(_PROVENANCE, name="provenance"),
        CheckConstraint(
            "age_min >= 0 AND age_max >= 0 AND age_min <= age_max", name="age_range"
        ),
        CheckConstraint("family_size >= 2", name="family_size"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE")
    )
    ticket_category: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str] = mapped_column(
        String(8), default=PriceUnit.PERSON.value, server_default=PriceUnit.PERSON.value
    )
    # Concession bounds; without them child <= 17, senior >= 65, family 2+2.
    age_min: Mapped[int | None] = mapped_column(SmallInteger)
    age_max: Mapped[int | None] = mapped_column(SmallInteger)
    family_size: Mapped[int | None] = mapped_column(SmallInteger)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    source_url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    checked_at: Mapped[datetime | None]

    place: Mapped[Place] = relationship(back_populates="prices", lazy="raise")


class TransitFare(Base):
    """A public transport fare in a city, per ticket type and person category."""

    __tablename__ = "transit_fares"
    __table_args__ = (
        UniqueConstraint("city_slug", "ticket_type", "person_category"),
        CheckConstraint("amount >= 0", name="amount"),
        CheckConstraint(_CURRENCY, name="currency"),
        CheckConstraint(_PROVENANCE, name="provenance"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    city_slug: Mapped[str] = mapped_column(ForeignKey("cities.slug"), index=True)
    ticket_type: Mapped[str] = mapped_column(String(32))  # single, 24h, 72h...
    person_category: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    source_url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    checked_at: Mapped[datetime | None]
