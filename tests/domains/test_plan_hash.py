"""Canonical plan hash: stable under input order, rounds times, plan content only."""

from datetime import UTC, date, datetime
from uuid import UUID

from tuttitrip.planning.logic.plan_hash import canonical_plan, plan_hash

P1 = UUID("10000000-0000-0000-0000-000000000001")
P2 = UUID("10000000-0000-0000-0000-000000000002")
DAY = date(2026, 10, 9)


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=UTC)


def test_visits_are_in_canonical_order() -> None:
    visits = [(P2, at(12, 0), at(13, 0)), (P1, at(9, 0), at(10, 0))]
    one = plan_hash([(DAY, visits)])
    two = plan_hash([(DAY, list(reversed(visits)))])
    assert one == two
    assert len(one) == 12


def test_times_are_rounded_to_five_minutes() -> None:
    def hash_of(minute: int, second: int = 0) -> str:
        return plan_hash([(DAY, [(P1, at(9, minute, second), at(10, 0))])])

    assert hash_of(1) == hash_of(0)  # 09:01 rounds to 09:00
    assert hash_of(2, 29) == hash_of(0)
    assert hash_of(3) == hash_of(5)  # 09:03 rounds to 09:05
    assert hash_of(7) != hash_of(0)


def test_hash_covers_only_the_plan_itself() -> None:
    content = canonical_plan([(DAY, [(P1, at(9, 0), at(10, 0))])], nights=2)
    assert set(content) == {"days", "nights"}  # no people, scores or amounts


def test_different_plans_differ() -> None:
    day = [(P1, at(9, 0), at(10, 0))]
    other = [(P2, at(9, 0), at(10, 0))]
    base = plan_hash([(DAY, day)])
    assert plan_hash([(DAY, other)]) != base
    assert plan_hash([(DAY, day)], nights=1) != base
