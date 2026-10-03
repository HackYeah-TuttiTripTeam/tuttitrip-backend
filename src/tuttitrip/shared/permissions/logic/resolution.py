"""Effective permissions: the highest level granted on a feature or an ancestor.

A grant ``(code, level)`` applies to the node ``code`` and every node below
it. The effective level of a feature is the maximum over all grants (from
roles, direct grants and the superadmin claim) on that feature or any of its
ancestors. Grants naming codes that are not in the registry (e.g. a feature
removed from the code) match nothing and are ignored.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from tuttitrip.shared.permissions.registry import (
    Access,
    Feature,
    is_admin_feature,
    is_leaf,
)


@dataclass(frozen=True, slots=True)
class Grant:
    """One grant: ``level`` on the feature ``feature`` and its subtree.

    ``feature`` is a plain code because stored grants may outlive a feature.
    """

    feature: str
    level: Access


SUPERADMIN_GRANT = Grant(Feature.ROOT, Access.WRITE)


class EffectivePermissions:
    """What a user may do: the resolved level for every registry feature."""

    __slots__ = ("_levels",)

    def __init__(self, levels: Mapping[Feature, Access]) -> None:
        self._levels = MappingProxyType(dict(levels))

    def level(self, feature: Feature | str) -> Access | None:
        """Resolved level on a feature.

        Args:
            feature: A registry feature (or its code).

        Returns:
            The level, or None when the user has no access (or the code is
            not in the registry).
        """
        try:
            return self._levels.get(Feature(feature))
        except ValueError:
            return None

    def allows(self, feature: Feature | str, level: Access) -> bool:
        """Whether the user holds at least ``level`` on ``feature``.

        Args:
            feature: A registry feature (or its code).
            level: Required level.

        Returns:
            True when access is granted; unknown codes are always denied.
        """
        held = self.level(feature)
        return held is not None and held.satisfies(level)

    def as_dict(self) -> dict[str, Access]:
        """Flattened map for clients, e.g. ``{"trips": "WRITE", ...}``.

        Returns:
            Every feature the user can at least read, in registry order.
        """
        return {feature.value: level for feature, level in self._levels.items()}


def resolve(
    grants: Iterable[Grant], *, superadmin: bool = False
) -> EffectivePermissions:
    """Resolve grants into effective permissions.

    Args:
        grants: Grants from the user's roles and direct grants.
        superadmin: The Auth0 roles claim contains ``admin``, which adds
            ``*`` WRITE (everything).

    Returns:
        The effective permissions.
    """
    best: dict[str, Access] = {}
    for grant in [*grants, *([SUPERADMIN_GRANT] if superadmin else [])]:
        current = best.get(grant.feature)
        if current is None or grant.level > current:
            best[grant.feature] = grant.level
    levels: dict[Feature, Access] = {}
    for feature in Feature:
        held = [best[node] for node in feature.lineage if node in best]
        if held:
            levels[feature] = max(held)
    return EffectivePermissions(levels)


def beyond_reach(actor: EffectivePermissions, grants: Iterable[Grant]) -> list[Grant]:
    """Grants the actor may not hand out: nobody grants more than they hold.

    Args:
        actor: The admin's effective permissions.
        grants: Grants the admin wants to give (directly or through a role).

    Returns:
        Grants above the actor's own level on that feature (empty when allowed).
    """
    return [g for g in grants if not actor.allows(g.feature, g.level)]


def invalid_for_default_role(grants: Iterable[Grant]) -> list[Grant]:
    """Grants the default ``user`` role must not hold.

    It may only list explicit leaves outside ``admin.*`` so that it never
    reaches administrative features, even after the tree grows.

    Args:
        grants: Proposed grants of the default role.

    Returns:
        Offending grants (unknown codes, groups or admin features).
    """
    invalid: list[Grant] = []
    for grant in grants:
        try:
            feature = Feature(grant.feature)
        except ValueError:
            invalid.append(grant)
            continue
        if not is_leaf(feature) or is_admin_feature(feature):
            invalid.append(grant)
    return invalid
