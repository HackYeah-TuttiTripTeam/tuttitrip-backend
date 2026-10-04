"""Who may remove a member, grant a role or leave a trip.

Source: "zarządza członkami (nie może usunąć co-hostów lub głównego hosta)".
The host never loses the role except by handing it over (``can_transfer_host``).
"""

from tuttitrip.trips.schemas import TripRole


def can_remove(actor: TripRole, target: TripRole) -> bool:
    """Whether ``actor`` may remove a member who has role ``target``.

    A co-host or host removes only people ranked below them, so a co-host
    removes members, the host removes members and co-hosts, and nobody
    removes the host (leaving is ``can_leave``).

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
    host here (``can_transfer_host`` does that).

    Args:
        actor: The caller's role.
        target: The member's current role.
        new: The requested role.

    Returns:
        True when the change is allowed.
    """
    return actor is TripRole.HOST and TripRole.HOST not in {target, new}


def can_leave(role: TripRole) -> bool:
    """Whether someone with ``role`` may leave the trip.

    The host may not: the trip always has a host, so they hand the role over first.

    Args:
        role: The leaver's role.

    Returns:
        True for a co-host or a member.
    """
    return role is not TripRole.HOST


def can_transfer_host(actor: TripRole, target: TripRole) -> bool:
    """Whether ``actor`` may hand the host role to a member who has role ``target``.

    Args:
        actor: The caller's role.
        target: The role of the member who would become host.

    Returns:
        True when the caller is the host and the target is not.
    """
    return actor is TripRole.HOST and target is not TripRole.HOST
