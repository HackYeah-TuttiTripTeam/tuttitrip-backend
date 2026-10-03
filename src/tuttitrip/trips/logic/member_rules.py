"""Who may remove a member and who may grant a role on a trip.

Source: "zarządza członkami (nie może usunąć co-hostów lub głównego hosta)".
The host never loses the role (no removal, no demotion, no transfer here).
"""

from tuttitrip.trips.schemas import TripRole


def can_remove(actor: TripRole, target: TripRole) -> bool:
    """Whether ``actor`` may remove a member who has role ``target``.

    A co-host or host removes only people ranked below them, so a co-host
    removes members, the host removes members and co-hosts, and nobody
    removes the host (or themselves, leaving a trip is out of scope).

    Args:
        actor: The caller's role.
        target: The role of the member to remove.

    Returns:
        True when the removal is allowed.
    """
    return actor.satisfies(TripRole.CO_HOST) and actor.rank > target.rank


def can_set_role(actor: TripRole, target: TripRole, new: TripRole) -> bool:
    """Whether ``actor`` may change a member from ``target`` to ``new``.

    Only the host changes roles, never the host's own, and nobody is made
    host (handing over the host role is out of scope).

    Args:
        actor: The caller's role.
        target: The member's current role.
        new: The requested role.

    Returns:
        True when the change is allowed.
    """
    return actor is TripRole.HOST and TripRole.HOST not in {target, new}
