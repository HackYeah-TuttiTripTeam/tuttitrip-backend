"""Oczekiwane wyniki z sekcji 7 ``docs/algorytm.md`` jako stałe testowe.

To liczby implementacji referencyjnej (autor algorytmu). Test porównawczy (``skip``)
jest aktywny dopiero po przeniesieniu ``demo_data.py`` do fixtures,
bo bez jego danych liczb nie da się odtworzyć. Spójność samej tabeli
(podłoga, ``r``, Jain) sprawdzamy już teraz, bez solvera.
"""

from dataclasses import dataclass
from decimal import Decimal

SKIP_REASON = (
    "Czeka na demo_data.py z implementacji referencyjnej i solver (backend#50): "
    "bez tych danych liczby z sekcji 7 nie dają się odtworzyć. Zadanie: #152."
)


@dataclass(frozen=True, slots=True)
class ExpectedPerson:
    """Wiersz tabeli sekcji 7."""

    u_star: float
    u: float
    r_percent: int
    floor: float


REFERENCE_PEOPLE = {
    "babcia": ExpectedPerson(u_star=64.6, u=63.6, r_percent=99, floor=30.0),
    "kasia": ExpectedPerson(u_star=62.2, u=55.9, r_percent=91, floor=35.0),
    "tomek": ExpectedPerson(u_star=43.8, u=40.8, r_percent=94, floor=26.3),
    "ty": ExpectedPerson(u_star=60.3, u=51.4, r_percent=87, floor=30.0),
}
REFERENCE_COST = Decimal(1475)
REFERENCE_HASH = "9defef8adc3f"
REFERENCE_LODGING = "apartament_basen"
REFERENCE_JAIN = 0.998
REFERENCE_MIN_R = 0.87
# Plan dnia po dniu (klucze miejsc miasta testowego, kolejność jak w opisie).
REFERENCE_PLAN = (
    ("muzeum_miejskie", "restauracja_indyjska"),
    ("hevelianum", "bar_mleczny", "kawiarnia_w_ogrodzie"),
    ("park_oliwski", "planszowki", "pizzeria"),
)

# Stałe poniżej czekają na pominięty test (#152); nie używa ich żaden aktywny test.
SOLO_U = 82.1  # Ty, 2 dni, 500 do 800 zł; każdy ma r = 100%
# Dosłowny cytat ze specyfikacji ("514 zł zamiast 500 zł"); relacja do δ = 0,15 jest
# niejednoznaczna, więc nie wyprowadzamy z niej niczego.
SOLO_UNVERIFIED_PRICE = (Decimal(500), Decimal(514))

OVER_BUDGET_COST = Decimal(1198)
OVER_BUDGET_EXCESS = Decimal(98)  # ponad B_do = 1100
# c_strict ani ΔU nie ma w specyfikacji, więc κ porównujemy tylko z wyniku solvera.
OVER_BUDGET_KAPPA = 18.7  # zł za punkt
