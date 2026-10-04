"""Places DTOs: the shared tag taxonomy, categories and the catalog read model.

Every price and every opening-hours entry carries its source, the date it was
checked and a ``verified`` mark, so that no figure comes from a language model
unnoticed. ``PlaceTag`` is the single taxonomy shared by place tags and by the
interests of people on a trip (spec section 4.1 matches them one to one).
"""

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class PlaceTag(StrEnum):
    """Interest taxonomy shared by places and by people's interests."""

    HISTORY = "history"
    ARCHITECTURE = "architecture"
    ART = "art"
    MUSEUMS = "museums"
    SCIENCE = "science"
    RELIGION = "religion"
    MUSIC = "music"
    NATURE = "nature"
    PARKS = "parks"
    VIEWS = "views"
    BEACHES = "beaches"
    SPORT = "sport"
    ADVENTURE = "adventure"
    FAMILY = "family"
    KIDS = "kids"
    NIGHTLIFE = "nightlife"
    SHOPPING = "shopping"
    MARKETS = "markets"
    LOCAL_FOOD = "local_food"
    STREET_FOOD = "street_food"
    RELAXATION = "relaxation"
    ANIMALS = "animals"
    PLAYGROUND = "playground"
    WATER = "water"
    CYCLING = "cycling"
    WELLNESS = "wellness"


class PlaceCategory(StrEnum):
    """What kind of place it is; ``lodging`` places are stays priced per night."""

    ATTRACTION = "attraction"
    MUSEUM = "museum"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    PARK = "park"
    VIEWPOINT = "viewpoint"
    SHOPPING = "shopping"
    NIGHTLIFE = "nightlife"
    ENTERTAINMENT = "entertainment"
    LODGING = "lodging"


class PlaceSource(StrEnum):
    """Where the place record comes from. Google Places data is never stored."""

    SHEET = "sheet"
    OSM = "osm"


class OsmType(StrEnum):
    """OpenStreetMap element type; ``osm_id`` is unique only per type."""

    NODE = "node"
    WAY = "way"
    RELATION = "relation"


class TicketCategory(StrEnum):
    """Who a ticket price is for."""

    ADULT = "adult"
    CHILD = "child"
    SENIOR = "senior"
    STUDENT = "student"
    REDUCED = "reduced"  # general concession, the source does not say for whom
    FAMILY = "family"


class DietTag(StrEnum):
    """Diet a place can serve; shared with the diets in people's preferences."""

    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"
    GLUTEN_FREE = "gluten_free"
    LACTOSE_FREE = "lactose_free"
    NUT_FREE = "nut_free"
    HALAL = "halal"
    KOSHER = "kosher"


class Cuisine(StrEnum):
    """Cuisine of a restaurant; shared with the cuisine minima (e.g. Indian)."""

    POLISH = "polish"
    ITALIAN = "italian"
    FRENCH = "french"
    GERMAN = "german"
    BRITISH = "british"
    SPANISH = "spanish"
    GREEK = "greek"
    TURKISH = "turkish"
    MIDDLE_EASTERN = "middle_eastern"
    INDIAN = "indian"
    CHINESE = "chinese"
    JAPANESE = "japanese"
    THAI = "thai"
    VIETNAMESE = "vietnamese"
    MEXICAN = "mexican"
    AMERICAN = "american"
    INTERNATIONAL = "international"


class Amenity(StrEnum):
    """Lodging amenity checked against the requirements contract."""

    POOL = "pool"
    KITCHEN = "kitchen"
    PARKING = "parking"
    FAMILY_ROOM = "family_room"
    WIFI = "wifi"
    AIR_CONDITIONING = "air_conditioning"
    BREAKFAST = "breakfast"
    PETS_ALLOWED = "pets_allowed"
    ELEVATOR = "elevator"
    WHEELCHAIR_ACCESSIBLE = "wheelchair_accessible"
    WASHING_MACHINE = "washing_machine"
    BALCONY = "balcony"
    CRIB = "crib"
    PLAYGROUND = "playground"


class PriceUnit(StrEnum):
    """What a price is charged for: E6 multiplies ``night`` prices by the nights."""

    PERSON = "person"
    NIGHT = "night"
    GROUP = "group"


class Weekday(StrEnum):
    """Day of the week, as a key of the weekly opening hours."""

    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")


def _clock(value: str) -> str:
    if not _CLOCK.match(value):
        msg = f"expected HH:MM (00:00 to 24:00), got {value!r}"
        raise ValueError(msg)
    return value


Clock = Annotated[str, AfterValidator(_clock)]


class TimeRange(BaseModel):
    """One opening interval in the city's local time (``close`` may be 24:00)."""

    open: Clock
    close: Clock

    @model_validator(mode="after")
    def _opens_before_it_closes(self) -> Self:
        if self.open >= self.close:
            msg = "open must be earlier than close (split intervals at midnight)"
            raise ValueError(msg)
        return self


class OpeningHours(BaseModel):
    """Simple weekly opening hours in the city's local time zone.

    A missing or empty weekday means closed that day. Parsing raw OSM
    ``opening_hours`` strings belongs to the import, not to the solver.
    """

    weekly: dict[Weekday, list[TimeRange]] = Field(default_factory=dict)
    closed_dates: list[date] = Field(
        default_factory=list, description="Dates closed regardless of the weekday."
    )


class PlaceHours(BaseModel):
    """Opening hours with their provenance."""

    opening_hours: OpeningHours | None = Field(
        description="Null when no hours are known."
    )
    source_url: str | None
    verified: bool = Field(description="True only for hours taken from a source.")
    checked_at: AwareDatetime | None


class PlacePriceRead(BaseModel):
    """A ticket price with its provenance.

    Free admission is a row with ``amount`` 0 and ``verified`` true. No row
    means the price is unknown (unverified, the delta in E6). Without age
    bounds the defaults apply: child up to 17, senior from 65, family 2+2.
    """

    # One schema for both directions: the linter takes places in a request body,
    # and a Decimal would otherwise split it into -Input and -Output.
    model_config = ConfigDict(
        from_attributes=True, json_schema_mode_override="serialization"
    )

    ticket_category: TicketCategory
    unit: PriceUnit = Field(description="Charged per person, per night or per group.")
    age_min: int | None = Field(description="Youngest age the concession covers.")
    age_max: int | None = Field(description="Oldest age the concession covers.")
    family_size: int | None = Field(description="People covered by a family ticket.")
    amount: Decimal
    currency: str
    source_url: str | None
    verified: bool
    checked_at: AwareDatetime | None


class CityRead(BaseModel):
    """A city the planner covers."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    country: str = Field(description="ISO 3166-1 alpha-2.")
    timezone: str = Field(description="IANA zone; opening hours are in this time.")
    currency: str = Field(description="ISO 4217.")
    center_lat: float
    center_lon: float
    bbox_south: float
    bbox_west: float
    bbox_north: float
    bbox_east: float

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown time zone {value!r}"
            raise ValueError(msg) from exc
        return value


class PlaceRead(BaseModel):
    """A catalog place with its planning attributes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    city_slug: str
    name: str
    category: PlaceCategory
    tags: list[PlaceTag]
    lat: float
    lon: float
    osm_type: OsmType | None
    osm_id: int | None
    google_place_id: str | None = Field(
        description="Only for the Places UI Kit card; never used by the algorithm."
    )
    hours: PlaceHours
    prices: list[PlacePriceRead] = Field(
        description="Free means a row with amount 0 and verified true. "
        "No row means the price is unknown (unverified)."
    )
    typical_visit_min: int = Field(description="Typical visit length (tau_p), min.")
    segment_km: float = Field(description="Walking segment at the place (d_p), km.")
    transfer_min: int = Field(description="Fixed transfer time (transfer_p), min.")
    queue_min: int = Field(description="Typical queue, min.")
    stairs: float = Field(ge=0, le=1, description="Stairs burden, 0 to 1.")
    wheelchair: bool | None
    indoor: bool | None = Field(
        description="Null when unknown (not the same as outdoors)."
    )
    iconic: bool
    cuisine: Cuisine | None
    diet_tags: list[DietTag]
    amenities: list[Amenity] = Field(description="Lodging amenities.")
    source_key: str | None = Field(description="Stable key of the import row.")
    source: PlaceSource
