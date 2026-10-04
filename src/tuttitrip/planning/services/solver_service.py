"""The solver the settings choose (docs/algorytm.md, section 9)."""

from dataclasses import dataclass

from tuttitrip.planning.logic.cpsat import CpSatLimits, CpSatSolver
from tuttitrip.planning.logic.solver import Solver, solve
from tuttitrip.shared.config.settings import get_settings


@dataclass(frozen=True, slots=True)
class SolverChoice:
    """A solver and what identifies it in the hash of a plan's input."""

    solver: Solver
    tag: str | None
    """Added to the input hash; None for the default, so old hashes stay valid."""


def configured_solver() -> SolverChoice:
    """The solver of ``TUTTITRIP_PLANNING__SOLVER`` with its limits.

    Returns:
        The deterministic local search by default, CP-SAT when switched on.
    """
    settings = get_settings().planning
    if settings.solver == "cp_sat":
        limits = CpSatLimits(
            settings.cpsat_max_deterministic_time, settings.cpsat_random_seed
        )
        return SolverChoice(CpSatSolver(limits), f"cp_sat:{limits.random_seed}")
    return SolverChoice(solve, None)
