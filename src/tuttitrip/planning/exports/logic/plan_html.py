"""An approved plan as an HTML page that Drive converts to a Google Doc. Pure."""

import datetime as dt
from html import escape

from tuttitrip.planning.plans.logic.ics import stop_description
from tuttitrip.planning.plans.schemas import PlanRead, PlanStop

TIME_FORMAT = "%H:%M"


def _stop(stop: PlanStop, currency: str) -> str:
    when = f"{stop.start.strftime(TIME_FORMAT)}-{stop.end.strftime(TIME_FORMAT)}"
    where = escape(stop.address or stop.name)
    lines = "<br>".join(escape(x) for x in stop_description(stop, currency).split("\n"))
    return (
        f"<li><b>{when}</b> {escape(stop.name)}<br>{where}<br>"
        f"<small>{lines}</small></li>"
    )


def _day(index: int, date: dt.date | None, stops: str) -> str:
    title = f"Dzień {index}" + (f", {date.isoformat()}" if date else "")
    return f"<h2>{title}</h2><ol>{stops}</ol>"


def build_html(plan: PlanRead, trip_name: str) -> str:
    """Render a plan version day by day.

    Args:
        plan: The stored version.
        trip_name: Title of the trip.

    Returns:
        A complete HTML document.
    """
    currency = plan.budget.currency
    days = "".join(
        _day(day.index, day.date, "".join(_stop(s, currency) for s in day.items))
        for day in plan.days
    )
    cost = f"{plan.budget.cost:.2f} {currency}"
    return (
        '<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8">'
        f"<title>{escape(trip_name)}</title></head><body>"
        f"<h1>{escape(trip_name)}</h1>"
        f"<p>Wersja planu {plan.version} ({plan.plan_hash}). Koszt: {cost}.</p>"
        f"{days}</body></html>"
    )
