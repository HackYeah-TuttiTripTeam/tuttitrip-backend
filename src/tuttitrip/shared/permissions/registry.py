"""Feature registry: every permissionable feature, as a tree defined in code.

Codes are dotted paths that mirror the domains and subdomains (``trips``,
``trips.members``). The parent of ``a.b`` is ``a`` and the parent of every
top-level code is the root ``*``. A grant on a node applies to the whole
subtree below it. A group is a node with children; endpoints require leaves
only (a group's own data gets a ``<group>.core`` leaf), so default roles can
list leaves explicitly. Everything only administrators may do lives under
``admin.*``.

Add a node by adding a ``Feature`` member and its Polish description in
``DESCRIPTIONS`` (tests check that every node has one and that its parent
exists). Other modules refer to features only through ``Feature`` members,
never through string literals.

This module is pure (standard library only): pure logic and DTOs may use it.
"""

from enum import StrEnum, unique
from typing import Final, override


@unique
class Access(StrEnum):
    """Access level. ``WRITE`` implies ``READ`` (``READ < WRITE``)."""

    READ = "READ"
    WRITE = "WRITE"

    @property
    def rank(self) -> int:
        """Position in the ordering (higher means more access).

        Returns:
            1 for ``READ``, 2 for ``WRITE``.
        """
        return _RANK[self]

    def satisfies(self, required: Access) -> bool:
        """Whether holding this level is enough for ``required``.

        Args:
            required: The level an operation needs.

        Returns:
            True when this level is at least ``required``.
        """
        return self.rank >= required.rank

    @override
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Access):
            return NotImplemented
        return self.rank < other.rank

    @override
    def __le__(self, other: object) -> bool:
        if not isinstance(other, Access):
            return NotImplemented
        return self.rank <= other.rank

    @override
    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Access):
            return NotImplemented
        return self.rank > other.rank

    @override
    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Access):
            return NotImplemented
        return self.rank >= other.rank


_RANK: Final[dict[Access, int]] = {Access.READ: 1, Access.WRITE: 2}

ROOT_CODE: Final = "*"
SEPARATOR: Final = "."


@unique
class Feature(StrEnum):
    """A node of the feature tree. The value is its dotted code."""

    ROOT = ROOT_CODE

    ACCOUNTS = "accounts"
    ACCOUNTS_PROFILE = "accounts.profile"

    ADMIN = "admin"
    ADMIN_PERMISSIONS = "admin.permissions"
    ADMIN_USERS = "admin.users"
    ADMIN_PLANNING_WEIGHTS = "admin.planning_weights"
    ADMIN_DEMO = "admin.demo"

    TRIPS = "trips"
    TRIPS_CORE = "trips.core"
    TRIPS_MEMBERS = "trips.members"
    TRIPS_INVITATIONS = "trips.invitations"
    TRIPS_VOTE_LINKS = "trips.vote_links"

    PROFILES = "profiles"
    PROFILES_CORE = "profiles.core"
    PROFILES_PREFERENCES = "profiles.preferences"
    PROFILES_FEEDBACK = "profiles.feedback"

    INTERVIEW = "interview"

    PLANNING = "planning"
    PLANNING_PROPOSALS = "planning.proposals"
    PLANNING_PLANS = "planning.plans"
    PLANNING_FAIRNESS = "planning.fairness"
    PLANNING_LINTER = "planning.linter"

    ACCOMMODATION = "accommodation"

    SEARCH = "search"

    EXPENSES = "expenses"
    EXPENSES_CORE = "expenses.core"
    EXPENSES_SETTLEMENT = "expenses.settlement"

    JOBS = "jobs"

    PLACES = "places"
    PLACES_CATALOG = "places.catalog"

    MCP = "mcp"
    NOTIFICATIONS = "notifications"

    @property
    def parent(self) -> Feature | None:
        """The enclosing group.

        Returns:
            The parent node, or None for the root.
        """
        if self is Feature.ROOT:
            return None
        head, _, _ = self.value.rpartition(SEPARATOR)
        return Feature(head or ROOT_CODE)

    @property
    def lineage(self) -> tuple[Feature, ...]:
        """This node and all its ancestors, nearest first, root last.

        Returns:
            For ``trips.members``: ``(trips.members, trips, *)``.
        """
        chain: list[Feature] = []
        node: Feature | None = self
        while node is not None:
            chain.append(node)
            node = node.parent
        return tuple(chain)

    @property
    def description(self) -> str:
        """Polish description for the admin UI and OpenAPI.

        Returns:
            The description from ``DESCRIPTIONS``.
        """
        return DESCRIPTIONS[self]


DESCRIPTIONS: Final[dict[Feature, str]] = {
    Feature.ROOT: "Wszystkie funkcjonalności aplikacji",
    Feature.ACCOUNTS: "Konto użytkownika",
    Feature.ACCOUNTS_PROFILE: "Własne konto: tożsamość, uprawnienia i zmiana nazwy",
    Feature.ADMIN: "Administracja (tylko dla administratorów)",
    Feature.ADMIN_PERMISSIONS: "Role, uprawnienia, ich przydział i dziennik zmian",
    Feature.ADMIN_USERS: "Zarządzanie kontami innych użytkowników",
    Feature.ADMIN_PLANNING_WEIGHTS: "Parametry algorytmu planowania",
    Feature.ADMIN_DEMO: "Reset konta demo",
    Feature.TRIPS: "Wyjazdy",
    Feature.TRIPS_CORE: "Wyjazd: tworzenie, lista i dane podstawowe",
    Feature.TRIPS_MEMBERS: "Uczestnicy wyjazdu i ich role",
    Feature.TRIPS_INVITATIONS: "Zaproszenia na wyjazd",
    Feature.TRIPS_VOTE_LINKS: "Linki głosowe dla osób bez konta i wynik głosowania",
    Feature.PROFILES: "Profile osób na wyjeździe",
    Feature.PROFILES_CORE: "Profile: osoby, wagi, grupy wiekowe",
    Feature.PROFILES_PREFERENCES: "Preferencje uczestników",
    Feature.PROFILES_FEEDBACK: "Opinie i oceny atrakcji",
    Feature.INTERVIEW: "Wywiad z asystentem AI",
    Feature.PLANNING: "Planowanie wyjazdu",
    Feature.PLANNING_PROPOSALS: "Propozycja planu: wysłanie, zatwierdzenie i uwagi",
    Feature.PLANNING_PLANS: "Plan z miarą sprawiedliwości, księgą i werdyktami",
    Feature.PLANNING_FAIRNESS: "Ocena sprawiedliwości planu",
    Feature.PLANNING_LINTER: "Sprawdzanie planu (godziny, dystanse, budżet)",
    Feature.ACCOMMODATION: "Wymagania wobec noclegu",
    Feature.SEARCH: "Wyszukiwanie",
    Feature.EXPENSES: "Wydatki i rozliczenia",
    Feature.EXPENSES_CORE: "Wydatki: lista i dodawanie",
    Feature.EXPENSES_SETTLEMENT: "Rozliczenie wydatków",
    Feature.JOBS: "Zadania w tle (stan i anulowanie)",
    Feature.PLACES: "Katalog miejsc",
    Feature.PLACES_CATALOG: "Katalog miejsc i miast: ceny i godziny ze źródłem",
    Feature.MCP: "Serwer MCP: własne dane w Claude, ChatGPT i innych klientach",
    Feature.NOTIFICATIONS: "Powiadomienia: skrzynka, licznik i strumień na żywo",
}


def children(node: Feature) -> list[Feature]:
    """Direct children of a node, in declaration order.

    Args:
        node: The parent node.

    Returns:
        Nodes whose parent is ``node``.
    """
    return [f for f in Feature if f.parent is node]


def is_leaf(node: Feature) -> bool:
    """Whether a node has no children.

    Args:
        node: The node.

    Returns:
        True for leaves.
    """
    return not children(node)


def is_admin_feature(node: Feature) -> bool:
    """Whether a node is ``admin``, under it, or an ancestor of it (``*``).

    Args:
        node: The node.

    Returns:
        True when a grant on it reaches administrative features.
    """
    return Feature.ADMIN in node.lineage or node is Feature.ROOT


# Roles the code relies on (seeded by a migration, never deleted).
# `user` applies to every authenticated user without an assignment and may
# only hold explicit leaves outside `admin.*`. `superadmin` (`*` WRITE) comes
# only from the Auth0 claim: it cannot be assigned, edited or deleted.
DEFAULT_ROLE: Final = "user"
SUPERADMIN_ROLE: Final = "superadmin"
SYSTEM_ROLES: Final = frozenset({DEFAULT_ROLE, SUPERADMIN_ROLE})
