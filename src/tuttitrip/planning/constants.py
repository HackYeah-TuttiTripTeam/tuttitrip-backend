"""Fixed values of the planning specification that are not tunable parameters.

The tunable ones (``alpha``, ``theta``, ``rho`` ...) are in
``planning.logic.params.AlgorithmParams``. Section numbers refer to
``docs/algorytm.md``.
"""

from typing import Final

BURDEN_DISTANCE_SHARE: Final = 0.6
"""E1: share of the walking segment ``min(1, d_p / s_i)`` in the burden ``e_ip``."""

BURDEN_STAIRS_SHARE: Final = 0.2
"""E1: share of ``stairs_p * sensitivity_i`` in the burden ``e_ip``."""

BURDEN_QUEUE_SHARE: Final = 0.2
"""E1: share of ``min(1, queue_p / patience_i)`` in the burden ``e_ip``."""
