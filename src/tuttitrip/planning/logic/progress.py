"""Progress of a plan computation: the stages of docs/algorytm.md as step codes.

The computation reports where it is through a ``ProgressSink``, a plain callback.
A sink only observes: nothing the computation reads or returns depends on it, so
the plan (and its ``plan_hash``) is the same with and without one. Pure, standard
library only.

```
catalogue  E0/E1  filter the catalogue by the hard constraints
reference  E4     solo run per person for u*_i          (item i of n people)
search     E5     search the group plan under the floors f_i^eff
floors     E4     check the floors, r_i and Jain's index
budget     E6     strict and cheaper plans for the consent (item i of 2)
verdicts   E6     verdicts of the places, upgrades and the stored content
```
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class PlanStep(StrEnum):
    """Stage of the computation; the declaration order is the order of work."""

    CATALOGUE = "catalogue"
    REFERENCE = "reference"
    SEARCH = "search"
    FLOORS = "floors"
    BUDGET = "budget"
    VERDICTS = "verdicts"


PLAN_STEPS: tuple[PlanStep, ...] = tuple(PlanStep)
"""The stages in order; one that does not apply (``reference`` alone) is skipped."""


@dataclass(frozen=True, slots=True)
class PlanProgress:
    """One progress event."""

    step: PlanStep
    item: int | None = None
    """1-based number of the unit of work inside the stage, if it has units."""
    items: int | None = None
    """How many units the stage has; set exactly when ``item`` is."""

    @property
    def position(self) -> int:
        """1-based place of the stage among ``PLAN_STEPS``."""
        return PLAN_STEPS.index(self.step) + 1


ProgressSink = Callable[[PlanProgress], None]


def ignore_progress(_event: PlanProgress) -> None:
    """The sink that drops every event (the default of the computation)."""
