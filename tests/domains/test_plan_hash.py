"""Canonical plan hash: stable under input order, rounds times, money as text."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from tuttitrip.planning.logic.plan_hash import canonical_plan, plan_hash
from tuttitrip.planning.schemas import DomainScores

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")
P1 = UUID("10000000-0000-0000-0000-000000000001")
P2 = UUID("10000000-0000-0000-0000-000000000002")
DAY = date(2026, 10, 9)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=UTC)


def score(person: UUID, welfare: float) -> DomainScores:
    return DomainScores(
        person_id=person,
        lodging=None,
        food=50,
        attractions=60,
        pace=70,
        cost=80,
        welfare=welfare,
    )


def test_people_and_visits_are_in_canonical_order() -> None:
    visits = [(P2, at(12, 0), at(13, 0)), (P1, at(9, 0), at(10, 0))]
    one = plan_hash([(DAY, visits)], [score(A, 40), score(B, 50)], Decimal("100.00"))
    two = plan_hash(
        [(DAY, list(reversed(visits)))],
        [score(B, 50), score(A, 40)],
        Decimal("100.00"),
    )
    assert one == two
    assert len(one) == 12


def test_times_are_rounded_to_five_minutes() -> None:
    def hash_of(minute: int, second: int = 0) -> str:
        return plan_hash(
            [(DAY, [(P1, at(9, minute, second), at(10, 0))])],
            [score(A, 40)],
            Decimal(1),
        )

    assert hash_of(1) == hash_of(0)  # 09:01 rounds to 09:00
    assert hash_of(2, 29) == hash_of(0)
    assert hash_of(3) == hash_of(5)  # 09:03 rounds to 09:05
    assert hash_of(7) != hash_of(0)


def test_money_is_text_and_different_plans_differ() -> None:
    content = canonical_plan(
        [(DAY, [(P1, at(9, 0), at(10, 0))])], [score(A, 40)], Decimal("100.00")
    )
    assert content["cost"] == "100.00"
    base = plan_hash([(DAY, [(P1, at(9, 0), at(10, 0))])], [score(A, 40)], Decimal(100))
    other = plan_hash(
        [(DAY, [(P2, at(9, 0), at(10, 0))])], [score(A, 40)], Decimal(100)
    )
    pricier = plan_hash(
        [(DAY, [(P1, at(9, 0), at(10, 0))])], [score(A, 40)], Decimal(101)
    )
    assert len({base, other, pricier}) == 3
