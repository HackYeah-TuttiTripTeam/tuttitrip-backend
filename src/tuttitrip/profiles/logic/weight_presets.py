"""Weight presets and the weight-spread rule (section 2 of docs/algorytm.md)."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from tuttitrip.profiles.schemas import AgeGroup, WeightPreset

MAX_WEIGHT_RATIO = 3.0
CHILD_WEIGHT = 2.0
FOCUS_WEIGHT = 2.0


class WeightRatioError(ValueError):
    """The largest weight is more than three times the smallest."""


class FocusProfileRequiredError(ValueError):
    """The preset needs a chosen person and none was given."""


@dataclass(frozen=True, slots=True)
class WeightSubject:
    """What a preset needs to know about a person."""

    id: UUID
    age_group: AgeGroup


def validate_weights(weights: Sequence[float]) -> None:
    """Check ``w_max / w_min <= 3`` and positivity.

    Args:
        weights: Weights of all people on the trip.

    Raises:
        WeightRatioError: When a weight is not positive or the spread is too big.
    """
    if not weights:
        return
    low, high = min(weights), max(weights)
    finite = all(math.isfinite(w) for w in weights)
    if not finite or low <= 0 or high / low > MAX_WEIGHT_RATIO:
        msg = (
            f"Weights must be finite and positive with max/min <= {MAX_WEIGHT_RATIO:g}"
        )
        raise WeightRatioError(msg)


def preset_weights(
    preset: WeightPreset,
    people: Sequence[WeightSubject],
    focus: UUID | None = None,
) -> dict[UUID, float]:
    """Weights of everyone for a preset.

    ``dzien_babci`` is one global weight for the chosen person over the whole
    plan (the spec has a single ``w_i`` per person, no per-day weights).

    Args:
        preset: The preset.
        people: Everyone on the trip.
        focus: The chosen person, required by ``dzien_babci``.

    Returns:
        Map ``profile_id -> weight``.

    Raises:
        FocusProfileRequiredError: ``dzien_babci`` without a chosen person.
    """
    match preset:
        case WeightPreset.PO_ROWNO:
            return {p.id: 1.0 for p in people}
        case WeightPreset.POD_DZIECI:
            return {
                p.id: CHILD_WEIGHT if p.age_group is AgeGroup.CHILD else 1.0
                for p in people
            }
        case WeightPreset.DZIEN_BABCI:
            if focus is None:
                msg = "Preset dzien_babci needs focus_profile_id"
                raise FocusProfileRequiredError(msg)
            return {p.id: FOCUS_WEIGHT if p.id == focus else 1.0 for p in people}
