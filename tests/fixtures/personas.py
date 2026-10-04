"""Rodziny testowe: osoby i grupy jako DTO z ``profiles.schemas``.

Każda osoba ma komfort wpisany w całości (nie z domyślnych dla wieku), więc
zmiana ``age_defaults`` nie rusza fixtures. Pule ważności mają 10 punktów w
kolejności nocleg, jedzenie, atrakcje, tempo, koszt. Głosy ``vᵢₚ`` to oceny
miejsc (``want``/``dont_want``), weto jest osobnym twardym ograniczeniem E0.
Osoby i grupy powstają od nowa przy każdym wywołaniu, w stałej kolejności.
"""

from dataclasses import dataclass, replace
from datetime import time
from uuid import UUID, uuid5

from tests.fixtures.city import place_id
from tuttitrip.places.schemas import DietTag, PlaceTag
from tuttitrip.profiles.feedback.schemas import RatingUpdate, RatingValue, ReasonCode
from tuttitrip.profiles.preferences.schemas import (
    Constraints,
    Diet,
    ImportancePool,
    MinTag,
    MinTagDomain,
    PreferencesWrite,
)
from tuttitrip.profiles.schemas import ProfileCreate, WeightItem, WeightsUpdate

_NAMESPACE = UUID("8d2e5f10-47a3-4c6b-b1d9-0e7a6c3f2b44")
CHILD_WEIGHT = 2.0


@dataclass(frozen=True, slots=True)
class Persona:
    """Osoba z wyjazdu: profil, waga, preferencje, głosy i weta (klucze miejsc)."""

    key: str
    profile: ProfileCreate
    weight: float
    preferences: PreferencesWrite
    ratings: dict[str, RatingUpdate]
    vetoes: tuple[str, ...] = ()

    @property
    def id(self) -> UUID:
        """Stałe id profilu (z klucza osoby)."""
        return uuid5(_NAMESPACE, self.key)

    @property
    def pool(self) -> ImportancePool:
        """Pula ważności osoby (w fixtures zawsze podana)."""
        if self.preferences.importance_pool is None:
            msg = f"{self.key} has no importance pool"
            raise ValueError(msg)
        return self.preferences.importance_pool

    def rating_value(self, place_key: str) -> RatingValue:
        """Głos osoby na miejsce, ``neutral`` gdy jej nie oceniła."""
        rating = self.ratings.get(place_key)
        return rating.value if rating else RatingValue.NEUTRAL

    def veto_ids(self) -> list[UUID]:
        return [place_id(k) for k in self.vetoes]


@dataclass(frozen=True, slots=True)
class Group:
    """Grupa osób jednego wyjazdu."""

    key: str
    people: tuple[Persona, ...]

    def weights_update(self) -> WeightsUpdate:
        """Wagi grupy jako payload API (ręczne wagi po id profilu)."""
        return WeightsUpdate(
            weights=[WeightItem(profile_id=p.id, weight=p.weight) for p in self.people]
        )


def _pool(
    lodging: int, food: int, attractions: int, pace: int, cost: int
) -> ImportancePool:
    return ImportancePool(
        lodging=lodging, food=food, attractions=attractions, pace=pace, cost=cost
    )


def _want(*keys: str) -> dict[str, RatingUpdate]:
    return {k: RatingUpdate(value=RatingValue.WANT) for k in keys}


def _dont(key: str, reason: ReasonCode) -> dict[str, RatingUpdate]:
    return {key: RatingUpdate(value=RatingValue.DONT_WANT, reason_code=reason)}


def _person(  # ruff: ignore[too-many-arguments] flat record of the per-person inputs
    key: str,
    name: str,
    age: int,
    *,
    comfort: tuple[float, float, int, float, int],
    nap: tuple[time, int] | None = None,
    floor: int = 30,
    weight: float = 1.0,
    pool: ImportancePool,
    interests: dict[PlaceTag, float],
    ratings: dict[str, RatingUpdate],
    vetoes: tuple[str, ...] = (),
    min_tags: tuple[MinTag, ...] = (),
    diet: Diet | None = None,
    constraints: Constraints | None = None,
) -> Persona:
    segment_km, daily_km, active_min, stairs, queue = comfort
    return Persona(
        key=key,
        profile=ProfileCreate(
            display_name=name,
            age=age,
            segment_km=segment_km,
            daily_km=daily_km,
            active_min=active_min,
            stairs_sensitivity=stairs,
            queue_patience_min=queue,
            nap_start=nap[0] if nap else None,
            nap_minutes=nap[1] if nap else 0,
            floor=floor,
        ),
        weight=weight,
        preferences=PreferencesWrite(
            interests=interests,
            diet=diet or Diet(),
            min_tags=list(min_tags),
            constraints=constraints or Constraints(),
            importance_pool=pool,
        ),
        ratings=ratings,
        vetoes=vetoes,
    )


def ty() -> Persona:
    """Dorosły organizator (zarazem osoba solo z sekcji 7)."""
    return _person(
        "ty",
        "Ty",
        38,
        comfort=(3.0, 12.0, 600, 0.2, 40),
        pool=_pool(2, 2, 3, 1, 2),
        interests={
            PlaceTag.HISTORY: 0.8,
            PlaceTag.MUSEUMS: 0.7,
            PlaceTag.ARCHITECTURE: 0.6,
            PlaceTag.LOCAL_FOOD: 0.7,
            PlaceTag.VIEWS: 0.5,
            PlaceTag.PARKS: 0.4,
            PlaceTag.SCIENCE: 0.3,
        },
        ratings=_want("muzeum_miejskie", "westerplatte", "bar_mleczny", "park_oliwski"),
    )


def kasia() -> Persona:
    """Sześciolatka: krótki odcinek, drzemka, podłoga 35, waga dziecka."""
    return _person(
        "kasia",
        "Kasia",
        6,
        comfort=(1.0, 4.0, 300, 0.6, 15),
        nap=(time(13, 0), 60),
        floor=35,
        weight=CHILD_WEIGHT,
        pool=_pool(1, 3, 4, 2, 0),
        interests={
            PlaceTag.KIDS: 1.0,
            PlaceTag.PLAYGROUND: 0.9,
            PlaceTag.ANIMALS: 0.9,
            PlaceTag.FAMILY: 0.8,
            PlaceTag.WATER: 0.6,
            PlaceTag.PARKS: 0.5,
            PlaceTag.SCIENCE: 0.4,
        },
        ratings=_want("zoo", "hevelianum", "planszowki", "park_oliwski", "pizzeria"),
    )


def tomek() -> Persona:
    """Trzynastolatek z minimum „indyjska” (jedzenie 4 z 10 punktów, k = 1)."""
    return _person(
        "tomek",
        "Tomek",
        13,
        comfort=(2.5, 9.0, 540, 0.1, 30),
        weight=CHILD_WEIGHT,
        pool=_pool(1, 4, 2, 1, 2),
        interests={
            PlaceTag.SCIENCE: 0.9,
            PlaceTag.STREET_FOOD: 0.8,
            PlaceTag.ADVENTURE: 0.7,
            PlaceTag.SPORT: 0.5,
            PlaceTag.VIEWS: 0.5,
            PlaceTag.MUSEUMS: 0.4,
            PlaceTag.HISTORY: 0.3,
        },
        ratings=_want(
            "hevelianum", "restauracja_indyjska", "wieza_widokowa", "planszowki"
        ),
        min_tags=(MinTag(domain=MinTagDomain.FOOD, tag="indian"),),
    )


def babcia() -> Persona:
    """Seniorka: wrażliwość na schody 0,9 (wieża odpada), weto na restaurację morską."""
    return _person(
        "babcia",
        "Babcia",
        70,
        comfort=(1.5, 6.0, 420, 0.9, 20),
        nap=(time(14, 0), 30),
        pool=_pool(3, 2, 1, 3, 1),
        interests={
            PlaceTag.PARKS: 0.8,
            PlaceTag.RELAXATION: 0.8,
            PlaceTag.HISTORY: 0.7,
            PlaceTag.LOCAL_FOOD: 0.6,
            PlaceTag.RELIGION: 0.6,
            PlaceTag.ARCHITECTURE: 0.6,
            PlaceTag.MUSEUMS: 0.5,
        },
        ratings=_want(
            "park_oliwski", "kawiarnia_w_ogrodzie", "muzeum_miejskie", "bar_mleczny"
        )
        | _dont("wieza_widokowa", ReasonCode.OTHER),
        vetoes=("restauracja_morska",),
        diet=Diet(allergies=["ryby"]),
    )


def reference_family() -> Group:
    """Grupa referencyjna z sekcji 7: Ty, Kasia (6), Tomek (13), Babcia."""
    return Group("reference", (ty(), kasia(), tomek(), babcia()))


def solo_traveller() -> Group:
    """Osoba solo z sekcji 7 (ta sama co „Ty”, więc klony = solo)."""
    return Group("solo", (ty(),))


def friends() -> Group:
    """Troje dorosłych o rozbieżnych gustach: weganka, nocny, muzealnik."""
    ola = _person(
        "ola",
        "Ola",
        29,
        comfort=(3.0, 12.0, 600, 0.2, 40),
        pool=_pool(2, 4, 2, 1, 1),
        interests={
            PlaceTag.LOCAL_FOOD: 0.9,
            PlaceTag.PARKS: 0.7,
            PlaceTag.RELAXATION: 0.6,
            PlaceTag.NATURE: 0.6,
        },
        ratings=_want("restauracja_indyjska", "kawiarnia_w_ogrodzie", "park_oliwski"),
        diet=Diet(tags=[DietTag.VEGAN]),
    )
    bartek = _person(
        "bartek",
        "Bartek",
        31,
        comfort=(4.0, 16.0, 660, 0.1, 45),
        pool=_pool(1, 2, 3, 2, 2),
        interests={
            PlaceTag.ADVENTURE: 0.9,
            PlaceTag.SPORT: 0.8,
            PlaceTag.NIGHTLIFE: 0.7,
            PlaceTag.VIEWS: 0.7,
            PlaceTag.STREET_FOOD: 0.6,
        },
        ratings=_want("wieza_widokowa", "westerplatte", "pizzeria")
        | _dont("muzeum_miejskie", ReasonCode.NOT_MY_STYLE),
    )
    cezary = _person(
        "cezary",
        "Cezary",
        27,
        comfort=(3.0, 11.0, 600, 0.2, 40),
        pool=_pool(2, 1, 5, 1, 1),
        interests={
            PlaceTag.ART: 0.9,
            PlaceTag.MUSEUMS: 0.9,
            PlaceTag.HISTORY: 0.8,
            PlaceTag.ARCHITECTURE: 0.8,
        },
        ratings=_want("muzeum_miejskie", "hevelianum", "westerplatte"),
        min_tags=(MinTag(domain=MinTagDomain.ATTRACTIONS, tag="museums"),),
    )
    return Group("friends", (ola, bartek, cezary))


def accessible_family() -> Group:
    """Użytkowniczka wózka, jej partner i dwuipółlatka: schody wykluczają miejsca."""
    marta = _person(
        "marta",
        "Marta",
        41,
        comfort=(2.0, 8.0, 480, 0.4, 30),
        pool=_pool(3, 2, 2, 2, 1),
        interests={
            PlaceTag.ART: 0.7,
            PlaceTag.MUSEUMS: 0.7,
            PlaceTag.PARKS: 0.6,
            PlaceTag.LOCAL_FOOD: 0.6,
        },
        ratings=_want("muzeum_miejskie", "park_oliwski", "kawiarnia_w_ogrodzie"),
        constraints=Constraints(wheelchair=True),
    )
    piotr = _person(
        "piotr",
        "Piotr",
        43,
        comfort=(3.0, 12.0, 600, 0.2, 40),
        pool=_pool(2, 3, 2, 1, 2),
        interests={
            PlaceTag.LOCAL_FOOD: 0.8,
            PlaceTag.FAMILY: 0.7,
            PlaceTag.PARKS: 0.6,
            PlaceTag.HISTORY: 0.5,
        },
        ratings=_want("bar_mleczny", "pizzeria", "zoo", "park_oliwski"),
    )
    hania = _person(
        "hania",
        "Hania",
        2,
        comfort=(1.0, 4.0, 240, 0.8, 10),
        nap=(time(13, 0), 90),
        weight=CHILD_WEIGHT,
        pool=_pool(2, 3, 3, 2, 0),
        interests={
            PlaceTag.ANIMALS: 1.0,
            PlaceTag.PLAYGROUND: 0.9,
            PlaceTag.KIDS: 0.9,
            PlaceTag.PARKS: 0.6,
        },
        ratings=_want("zoo", "park_oliwski", "planszowki"),
    )
    return Group("accessible", (marta, piotr, hania))


def clones(person: Persona, n: int) -> Group:
    """Grupa ``n`` identycznych klonów osoby (test spójności grupa a solo, sekcja 4).

    Klony mają osobne id, tę samą wagę, komfort, preferencje i głosy.
    """
    people = tuple(
        replace(person, key=f"{person.key}-klon-{i}") for i in range(1, n + 1)
    )
    return Group(f"klony-{person.key}-{n}", people)


def all_groups() -> dict[str, Group]:
    return {
        g.key: g
        for g in (reference_family(), solo_traveller(), friends(), accessible_family())
    }
