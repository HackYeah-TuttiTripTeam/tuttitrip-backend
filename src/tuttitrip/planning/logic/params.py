"""Parameters of the planning algorithm (docs/algorytm.md, section 6).

One immutable object: the defaults below are version 0, the administrator
stores new versions (backend#96) and a plan records the version it was computed
with. The values are uncalibrated starting points. Every field carries its
allowed range, so a value outside it is refused when the object is built
(pydantic validation, no I/O), whether it comes from the admin API or a row.
"""

from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator
from pydantic.dataclasses import dataclass as validated_dataclass

type Unit = Annotated[float, Field(ge=0, le=1)]
"""A share or probability."""


class ParamsOrderError(ValueError):
    """``verdict_iconic`` is not below ``verdict_fits``."""


@validated_dataclass(
    frozen=True,
    slots=True,
    config=ConfigDict(use_attribute_docstrings=True, extra="forbid", strict=False),
)
class AlgorithmParams:
    """Section 6 of the specification: defaults and allowed ranges."""

    alpha: Annotated[float, Field(ge=0, le=3)] = 1.0
    """Fairness slider: 0 utility, 1 Nash, 3 almost egalitarian (E5)."""
    kappa_attractions: Annotated[float, Field(gt=0, le=5)] = 0.6
    """Daily saturation of attractions (E2)."""
    kappa_food: Annotated[float, Field(gt=0, le=5)] = 1.2
    """Daily saturation of food (E2)."""
    tau_ref_min: Annotated[int, Field(ge=15, le=480)] = 90
    """Reference visit time of an attraction, min (E2)."""
    epsilon: Annotated[float, Field(gt=0, le=1)] = 0.01
    """Stability of the geometric mean (E1)."""
    lambda_floor: Annotated[float, Field(ge=0, le=1)] = 0.1
    """Added to every pool share before ``Z`` normalises it (E1)."""
    vote_weight: Unit = 0.7
    """``rho``: weight of an explicit vote against the interest profile (E1)."""
    unverified_markup: Annotated[float, Field(ge=0, le=1)] = 0.15
    """``delta``: markup of an unverified price (E6)."""
    uncertain_requirement: Unit = 0.4
    """``rho_unc``: points for an unconfirmed lodging requirement (E2)."""
    strong_preference: Annotated[float, Field(gt=0, lt=1)] = 0.4
    """``theta``: pool share that makes a preference strong (E5)."""
    smoothing: Annotated[float, Field(gt=0, le=100)] = 10.0
    """``s``: smoothing in ``r_i`` (E4)."""
    floor_share: Unit = 0.6
    """The floor is at most this share of the person's own maximum (E4)."""
    violation_penalty: Annotated[float, Field(ge=1, le=1_000_000)] = 1000.0
    """Penalty of a missed floor, own place or tag minimum (E5)."""
    cost_comfort: Annotated[float, Field(ge=0, le=100)] = 60.0
    """``q_cost`` at ``B_do`` (E2)."""
    good_reason_points: Annotated[float, Field(ge=0, le=100)] = 8.0
    """Gain of a strongly-preferring person that justifies exceeding the budget (E6)."""
    good_reason_min_r: Unit = 0.05
    """Rise of ``min r`` that justifies exceeding the budget (E6)."""
    good_reason_welfare: Unit = 0.03
    """Welfare gain over the cheaper plan required for approval (E6)."""
    cheaper_margin: Unit = 0.05
    """E6: the cheaper alternative costs at most ``c - margin * B_do`` (5%)."""
    verdict_fits: Annotated[float, Field(ge=-1, le=1)] = 0.1
    """Extension, outside v1.0: ``V_p`` from which a place "fits" (backend#51)."""
    verdict_iconic: Annotated[float, Field(ge=-1, le=1)] = -0.3
    """Extension, outside v1.0: lowest ``V_p`` of "iconic, but not yours"."""
    max_exceptional_nights: Annotated[int, Field(ge=0, le=30)] = 0
    """Extension, outside v1.0 (backend#71): nights that may use another base."""
    rain_outdoor_factor: Unit = 0.3
    """Extension, outside v1.0 (backend#74): ``u_ip`` factor outdoors in rain."""
    rain_indoor_weight: Unit = 0.7
    """Extension: ``u_ip * (outdoor_factor + indoor_weight * [indoor])`` in rain."""
    replan_change_penalty: Annotated[float, Field(ge=0, le=100)] = 0.2
    """Extension: ``J_replan`` loses this much per place added or removed."""
    replan_shift_penalty: Annotated[float, Field(ge=0, le=10)] = 0.001
    """Extension: ``J_replan`` loses this much per minute a kept visit moves."""
    own_place_match: Unit = 0.6
    """``m_ip`` from which a place counts as the person's own (E5)."""
    stairs_limit: Annotated[float, Field(gt=0, le=1)] = 0.9
    """E0: a place is rejected at ``stairs_p * sensitivity_i`` from here."""
    segment_factor: Annotated[float, Field(ge=1, le=10)] = 1.5
    """E0: a place is rejected when ``d_p > factor * s_i``."""
    no_data_match: Unit = 0.5
    """E1: ``m_ip`` when there is neither an interest profile nor a vote."""

    @model_validator(mode="after")
    def _verdict_bands_are_ordered(self) -> Self:
        if self.verdict_iconic >= self.verdict_fits:
            msg = "verdict_iconic must be below verdict_fits"
            raise ParamsOrderError(msg)
        return self


DEFAULT_PARAMS = AlgorithmParams()
