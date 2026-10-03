"""Place catalog ORM models: cities, places, ticket prices and transit fares."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tuttitrip.places.schemas import (
    OsmType,
    PlaceCategory,
    PlaceSource,
    TicketCategory,
)
from tuttitrip.shared.db.base import Base


def _in(
    column: str, values: type[PlaceCategory | PlaceSource | OsmType | TicketCategory]
) -> str:
    """SQL ``column IN (...)`` for the values of a string enum.

    Args:
        column: Column name.
        values: The enum class.

    Returns:
        A check expression.
    """
    return "{} IN ({})".format(column, ", ".join(f"'{v.value}'" for v in values))


class City(Base):
    """A city the planner covers; hours are in its local time zone."""

    __tablename__ = "cities"

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


class Place(Base):
    """A catalog place (attraction, restaurant, lodging...) with planning inputs."""

    __tablename__ = "places"
    __table_args__ = (
        # `osm_id` alone is not unique: a node and a way may share the number.
        UniqueConstraint("osm_type", "osm_id"),
        CheckConstraint(_in("category", PlaceCategory), name="category"),
        CheckConstraint(_in("source", PlaceSource), name="source"),
        CheckConstraint(_in("osm_type", OsmType), name="osm_type"),
        CheckConstraint("(osm_type IS NULL) = (osm_id IS NULL)", name="osm_ref"),
        CheckConstraint("stairs BETWEEN 0 AND 1", name="stairs"),
        CheckConstraint("lat BETWEEN -90 AND 90", name="lat"),
        CheckConstraint("lon BETWEEN -180 AND 180", name="lon"),
        CheckConstraint(
            "typical_visit_min >= 0 AND segment_km >= 0"
            " AND transfer_min >= 0 AND queue_min >= 0",
            name="non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    city_slug: Mapped[str] = mapped_column(ForeignKey("cities.slug"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(32), index=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=list, server_default=text("'{}'")
    )
    # Coordinates come from OSM or the sheet, never from Google.
    lat: Mapped[float]
    lon: Mapped[float]
    osm_type: Mapped[str | None] = mapped_column(String(8))
    osm_id: Mapped[int | None] = mapped_column(BigInteger)
    # Kept without limit (place IDs are exempt from the cache restrictions).
    google_place_id: Mapped[str | None] = mapped_column(String(200))
    # Weekly format with closed dates (`OpeningHours`); times are city-local.
    opening_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    hours_source_url: Mapped[str | None] = mapped_column(Text)
    hours_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    hours_checked_at: Mapped[datetime | None]
    typical_visit_min: Mapped[int]  # tau_p
    segment_km: Mapped[float] = mapped_column(default=0.0, server_default=text("0"))
    transfer_min: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    queue_min: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    stairs: Mapped[float] = mapped_column(default=0.0, server_default=text("0"))
    wheelchair: Mapped[bool | None]
    indoor: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    iconic: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    cuisine: Mapped[str | None] = mapped_column(String(100))
    diet_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=list, server_default=text("'{}'")
    )
    source: Mapped[str] = mapped_column(String(16))

    prices: Mapped[list[PlacePrice]] = relationship(
        back_populates="place", lazy="raise", order_by="PlacePrice.ticket_category"
    )


class PlacePrice(Base):
    """A ticket price (or lodging price per night) with its source."""

    __tablename__ = "place_prices"
    __table_args__ = (
        UniqueConstraint("place_id", "ticket_category"),
        CheckConstraint(_in("ticket_category", TicketCategory), name="ticket_category"),
        CheckConstraint("amount >= 0", name="amount"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("places.id", ondelete="CASCADE")
    )
    ticket_category: Mapped[str] = mapped_column(String(16))
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
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
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
