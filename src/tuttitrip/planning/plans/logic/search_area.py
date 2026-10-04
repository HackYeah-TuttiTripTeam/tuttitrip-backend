"""Area to search for lodging, from the chosen plan (backend#70).

The centre is the mean of the visited places' coordinates weighted by the visit
time ``tau_p``; the radius reaches the farthest of them. It is computed after the
plan is chosen and does not enter ``J``. Pure, standard library only.
"""

import math
from collections.abc import Sequence

from tuttitrip.places.schemas import PlaceRead

EARTH_RADIUS_M = 6_371_000
MIN_RADIUS_M = 500


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def search_area(visited: Sequence[PlaceRead]) -> tuple[float, float, int] | None:
    """Centre and radius of the area around the stay's attractions.

    Args:
        visited: The places of the plan.

    Returns:
        ``(lat, lon, radius_m)``, or None for a plan without visits.
    """
    if not visited:
        return None
    weights = [max(1, p.typical_visit_min) for p in visited]
    total = sum(weights)
    lat = math.fsum(p.lat * w for p, w in zip(visited, weights, strict=True)) / total
    lon = math.fsum(p.lon * w for p, w in zip(visited, weights, strict=True)) / total
    radius = max(_distance_m(lat, lon, p.lat, p.lon) for p in visited)
    return lat, lon, max(MIN_RADIUS_M, math.ceil(radius))
