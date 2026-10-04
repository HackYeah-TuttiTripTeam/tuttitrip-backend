"""Canonical plan hash: stable under input order, rounds times, plan content only."""

from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from tuttitrip.planning.logic.plan_hash import canonical_plan, plan_hash

P1 = UUID("10000000-0000-0000-0000-000000000001")
P2 = UUID("10000000-0000-0000-0000-000000000002")
DAY = date(2026, 10, 9)
WARSAW = ZoneInfo("Europe/Warsaw")


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=WARSAW)


def test_visits_are_in_canonical_order() -> None:
    visits = [(P2, at(12, 0), at(13, 0)), (P1, at(9, 0), at(10, 0))]
    one = plan_hash([(DAY, visits)], 0, WARSAW)
    two = plan_hash([(DAY, list(reversed(visits)))], 0, WARSAW)
    assert one == two
    assert len(one) == 12


def test_times_are_rounded_to_five_minutes() -> None:
    def hash_of(minute: int, second: int = 0) -> str:
        return plan_hash([(DAY, [(P1, at(9, minute, second), at(10, 0))])], 0, WARSAW)

    assert hash_of(1) == hash_of(0)  # 09:01 rounds to 09:00
    assert hash_of(2, 29) == hash_of(0)
    assert hash_of(3) == hash_of(5)  # 09:03 rounds to 09:05
    assert hash_of(7) != hash_of(0)


def test_hash_covers_only_the_plan_itself() -> None:
    content = canonical_plan([(DAY, [(P1, at(9, 0), at(10, 0))])], 2, WARSAW)
    assert set(content) == {"days", "nights"}  # no people, scores or amounts


def test_different_plans_differ() -> None:
    day = [(P1, at(9, 0), at(10, 0))]
    other = [(P2, at(9, 0), at(10, 0))]
    base = plan_hash([(DAY, day)], 0, WARSAW)
    assert plan_hash([(DAY, other)], 0, WARSAW) != base
    assert plan_hash([(DAY, day)], 1, WARSAW) != base


def test_hash_does_not_depend_on_the_zone_of_the_instants() -> None:
    local = [(P1, at(9, 0), at(10, 30)), (P2, at(12, 0), at(13, 0))]
    in_utc = [(p, s.astimezone(UTC), e.astimezone(UTC)) for p, s, e in local]
    assert plan_hash([(DAY, local)], 1, WARSAW) == plan_hash([(DAY, in_utc)], 1, WARSAW)
