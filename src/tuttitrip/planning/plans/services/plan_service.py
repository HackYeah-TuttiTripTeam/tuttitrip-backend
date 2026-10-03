"""Plan service: returns the fixed sample plan until the solver exists (#50)."""

from tuttitrip.planning.plans.logic.sample_plan import sample_plan
from tuttitrip.planning.plans.schemas import PlanCreate, PlanRead
from tuttitrip.trips.schemas import TripMembership


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
