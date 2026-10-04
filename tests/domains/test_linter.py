"""Linter rules of time and cost (E0): pure unit tests and the endpoint."""

from datetime import date, time
from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient

from tests.domains.test_schedule import MONDAY, place
from tests.shared.fakes import authorize
from tuttitrip.main import create_app
from tuttitrip.places.schemas import PlaceRead, Weekday
from tuttitrip.planning.linter.logic.rules import RULES, lint
from tuttitrip.planning.linter.schemas import (
    LintContext,
    LintDay,
    LintItem,
    LintPlan,
    LintReport,
    LintRequest,
)
from tuttitrip.shared.auth.schemas import AuthenticatedUser

CODES = ["closed_day", "opening_hours", "transfer", "budget", "unknown_place"]


def verified(place_: PlaceRead) -> PlaceRead:
    hours = place_.hours.model_copy(update={"verified": True})
    return place_.model_copy(update={"hours": hours})


def item(
    n: int | None,
    start: str,
    *,
    end: time | None = None,
    cost: Decimal = Decimal(0),
    price_verified: bool = True,
) -> LintItem:
    return LintItem(
        name=f"p{n}",
        place_id=None if n is None else UUID(int=n),
        start=time.fromisoformat(start),
        end=end,
        cost=cost,
        price_verified=price_verified,
    )


def run(
    places: list[PlaceRead],
    items: list[LintItem],
    *,
    day: date = MONDAY,
    budget: int = 1000,
    flex_pct: int = 0,
) -> LintReport:
    context = LintContext(
        places=places,
        timezone="Europe/Warsaw",
        budget=Decimal(budget),
        flex_pct=flex_pct,
    )
    return lint(LintPlan(days=[LintDay(day=day, items=items)]), context)


def result(report: LintReport, code: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
    return next(r for r in report.results if r.rule == code)


def test_closed_weekday_is_a_violation_with_place_and_day() -> None:
    museum = verified(place(1, days=[d for d in Weekday if d != Weekday.MON]))
    report = run([museum], [item(1, "10:00")])
    closed = result(report, "closed_day")
    assert closed.count == 1
    finding = closed.violations[0]
    assert (finding.place_name, finding.day, finding.position) == ("p1", MONDAY, 0)
    assert result(report, "opening_hours").count == 0


def test_closed_date_is_a_violation() -> None:
    zoo = verified(place(1, closed_dates=[MONDAY]))
    assert result(run([zoo], [item(1, "10:00")]), "closed_day").count == 1


def test_visit_ending_after_closing_breaks_opening_hours() -> None:
    zoo = verified(place(1, close="18:00", visit=90))
    report = run([zoo], [item(1, "17:30")])
    assert result(report, "opening_hours").count == 1
    assert result(report, "closed_day").count == 0
    assert run([zoo], [item(1, "16:30")]).count == 0


def test_stated_end_overrides_the_typical_visit() -> None:
    zoo = verified(place(1, close="18:00", visit=90))
    assert run([zoo], [item(1, "17:30", end=time(17, 50))]).count == 0


def test_missing_or_unverified_hours_only_warn() -> None:
    no_hours = place(1, open_=None)
    unverified = place(2, days=[d for d in Weekday if d != Weekday.MON])
    report = run([no_hours, unverified], [item(1, "10:00"), item(2, "12:00")])
    assert report.count == 0
    assert report.score == 0
    opening = result(report, "opening_hours")
    assert [w.place_name for w in opening.warnings] == ["p1", "p2"]
    assert "unverified" in opening.warnings[0].message


def test_transfer_gap_shorter_than_catalog_transfer() -> None:
    a, b = place(1, visit=60, transfer=10), place(2, visit=60, transfer=15)
    tight = run([a, b], [item(2, "11:10"), item(1, "10:00")])
    transfer = result(tight, "transfer")
    assert [v.place_name for v in transfer.violations] == ["p2"]
    assert transfer.violations[0].position == 0
    assert (
        result(run([a, b], [item(1, "10:00"), item(2, "11:15")]), "transfer").count == 0
    )


def test_first_stop_and_unknown_previous_stop_need_no_transfer() -> None:
    a = place(1, transfer=30)
    assert result(run([a], [item(1, "09:00")]), "transfer").count == 0
    unknown = run([a], [item(None, "10:00", end=time(11, 0)), item(1, "11:10")])
    assert result(unknown, "transfer").count == 1


def test_budget_uses_b_max_and_inflates_unverified_prices() -> None:
    a = place(1)
    over = [item(1, "10:00", cost=Decimal(1100))]
    assert result(run([a], over), "budget").count == 1
    assert result(run([a], over, flex_pct=10), "budget").count == 0
    inflated = [item(1, "10:00", cost=Decimal(900), price_verified=False)]
    assert result(run([a], inflated), "budget").count == 1  # 900 * 1.15 = 1035


def test_unrecognised_and_foreign_stops_are_flagged() -> None:
    report = run([place(1)], [item(None, "10:00"), item(9, "12:00"), item(1, "14:00")])
    unknown = result(report, "unknown_place")
    assert [v.position for v in unknown.violations] == [0, 1]


def test_report_lists_every_rule_in_order_and_is_deterministic() -> None:
    zoo = verified(place(1))
    first = run([zoo], [item(1, "10:00")])
    assert [r.rule for r in first.results] == CODES == [r.code for r in RULES]
    assert all(r.count == 0 for r in first.results)
    assert first.digest == run([zoo], [item(1, "10:00")]).digest
    assert first.digest != run([zoo], [item(1, "10:00", cost=Decimal(5000))]).digest


def test_score_is_the_weighted_sum() -> None:
    report = run([], [item(None, "10:00"), item(None, "12:00")])
    weight = result(report, "unknown_place").weight
    assert (report.count, report.score) == (2, 2 * weight)


def test_check_endpoint() -> None:
    zoo = verified(place(1, close="18:00", visit=90))
    request = LintRequest(
        plan=LintPlan(days=[LintDay(day=MONDAY, items=[item(1, "17:30")])]),
        context=LintContext(
            places=[zoo], timezone="Europe/Warsaw", budget=Decimal(100)
        ),
    )
    app = create_app()
    authorize(app, AuthenticatedUser(sub="auth0|tester"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/planning/linter/check", json=request.model_dump(mode="json")
        )
    assert response.status_code == 200
    body = response.json()
    assert [r["rule"] for r in body["results"]] == CODES
    assert body["count"] == 1
