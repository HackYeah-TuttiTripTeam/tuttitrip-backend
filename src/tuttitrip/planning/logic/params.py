"""Parameters of the planning algorithm (docs/algorytm.md, section 6).

One immutable object so the admin panel (backend#96) has a single place to read
and override them. The values are uncalibrated starting points. E0 and E1 use
only some of them; the rest belong to later stages but live here so there is
exactly one source of defaults.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlgorithmParams:
    """Default values of section 6 of the specification."""

    alpha: float = 1.0
    """Fairness slider: 0 utility, 1 Nash, 3 almost egalitarian (E5)."""
    kappa_attractions: float = 0.6
    """Daily saturation of attractions (E2)."""
    kappa_food: float = 1.2
    """Daily saturation of food (E2)."""
    tau_ref_min: int = 90
    """Reference visit time of an attraction, min (E2)."""
    epsilon: float = 0.01
    """Stability of the geometric mean (E1)."""
    lambda_floor: float = 0.1
    """Added to every pool share before ``Z`` normalises it (E1)."""
    vote_weight: float = 0.7
    """``rho``: weight of an explicit vote against the interest profile (E1)."""
    unverified_markup: float = 0.15
    """``delta``: markup of an unverified price (E6)."""
    uncertain_requirement: float = 0.4
    """``rho_unc``: points for an unconfirmed lodging requirement (E2)."""
    strong_preference: float = 0.4
    """``theta``: pool share that makes a preference strong (E5)."""
    smoothing: float = 10.0
    """``s``: smoothing in ``r_i`` (E4)."""
    floor_share: float = 0.6
    """The floor is at most this share of the person's own maximum (E4)."""
    violation_penalty: float = 1000.0
    """Penalty of a missed floor, own place or tag minimum (E5)."""
    cost_comfort: float = 60.0
    """``q_cost`` at ``B_do`` (E2)."""
    good_reason_points: float = 8.0
    """Gain of a strongly-preferring person that justifies exceeding the budget (E6)."""
    good_reason_min_r: float = 0.05
    """Rise of ``min r`` that justifies exceeding the budget (E6)."""
    good_reason_welfare: float = 0.03
    """Welfare gain over the cheaper plan required for approval (E6)."""
    cheaper_margin: float = 0.05
    """E6: the cheaper alternative costs at most ``c - margin * B_do`` (5%)."""
    verdict_fits: float = 0.1
    """Extension, outside v1.0: ``V_p`` from which a place "fits" (backend#51)."""
    verdict_iconic: float = -0.3
    """Extension, outside v1.0: lowest ``V_p`` of "iconic, but not yours"."""
    max_exceptional_nights: int = 0
    """Extension, outside v1.0 (backend#71): nights that may use another base."""
    rain_outdoor_factor: float = 0.3
    """Extension, outside v1.0 (backend#74): ``u_ip`` factor outdoors in rain."""
    rain_indoor_weight: float = 0.7
    """Extension: ``u_ip * (outdoor_factor + indoor_weight * [indoor])`` in rain."""
    replan_change_penalty: float = 0.2
    """Extension: ``J_replan`` loses this much per place added or removed."""
    replan_shift_penalty: float = 0.001
    """Extension: ``J_replan`` loses this much per minute a kept visit moves."""
    own_place_match: float = 0.6
    """``m_ip`` from which a place counts as the person's own (E5)."""
    stairs_limit: float = 0.9
    """E0: a place is rejected at ``stairs_p * sensitivity_i`` from here."""
    segment_factor: float = 1.5
    """E0: a place is rejected when ``d_p > factor * s_i``."""
    no_data_match: float = 0.5
    """E1: ``m_ip`` when there is neither an interest profile nor a vote."""


DEFAULT_PARAMS = AlgorithmParams()
