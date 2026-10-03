"""Plan service: returns fixed sample plans until the solver exists (#50)."""

from uuid import UUID

from tuttitrip.planning.plans.logic.sample_plan import Scenario, sample_plan
from tuttitrip.planning.plans.schemas import PlanCreate, PlanRead
from tuttitrip.trips.schemas import TripMembership

EXAMPLE_TRIP_ID = UUID("00000000-0000-4000-8000-000000000042")


def generate_plan(membership: TripMembership, params: PlanCreate | None) -> PlanRead:
    """Generate (stub) a plan for the trip the caller belongs to.

    Args:
        membership: Proof that the caller may use the trip.
        params: Requested knobs, or None for defaults.

    Returns:
        The fixed sample plan.
    """
    return sample_plan(membership.trip_id, params)


def latest_plan(membership: TripMembership) -> PlanRead:
    """Latest (stub) plan of the trip.

    Args:
        membership: Proof that the caller may use the trip.

    Returns:
        The fixed sample plan.
    """
    return sample_plan(membership.trip_id)


def openapi_examples() -> dict[str, dict[str, object]]:
    """Named OpenAPI examples of a plan: a group, one person, an approval.

    Returns:
        Example objects for the OpenAPI ``examples`` field.
    """
    return {
        name: {
            "summary": summary,
            "value": sample_plan(EXAMPLE_TRIP_ID, scenario=scenario).model_dump(
                mode="json"
            ),
        }
        for name, summary, scenario in (
            ("group", "Group of four (section 7)", Scenario.GROUP),
            ("solo", "One person (n = 1): show domains, not Jain", Scenario.SOLO),
            ("needs_approval", "Over B_do: 1198, +98, kappa 18.7", Scenario.APPROVAL),
        )
    }
