"""Match a saved place's title to a catalog place by name (pure).

No coordinates come with a Takeout list, so only the name decides. A title
matches when its normalised form equals a catalog name, or when one name's
words all appear in the other (``Wawel`` in ``Zamek Królewski Wawel``) and
exactly one place qualifies. Two places that qualify equally are ``ambiguous``:
the importer never guesses between them.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

_NON_WORD = re.compile(r"[^a-z0-9]+")
_STROKED = str.maketrans({"ł": "l", "Ł": "L"})


class MatchOutcome(StrEnum):
    """Result of matching one title."""

    MATCHED = "matched"
    NOT_IN_CATALOG = "not_in_catalog"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A catalog place to match against."""

    place_id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class NameMatch:
    """The outcome and, when matched, the place."""

    outcome: MatchOutcome
    place_id: UUID | None = None


def normalise(name: str) -> str:
    """Lower-case ASCII words of a name, joined by single spaces.

    Args:
        name: A place name in any script of Latin letters.

    Returns:
        The name without diacritics (``ł`` included) or punctuation.
    """
    stripped = unicodedata.normalize("NFKD", name.translate(_STROKED))
    ascii_name = "".join(c for c in stripped if not unicodedata.combining(c))
    return _NON_WORD.sub(" ", ascii_name.casefold()).strip()


def _contains(words: frozenset[str], other: frozenset[str]) -> bool:
    smaller, larger = sorted((words, other), key=len)
    return bool(smaller) and smaller <= larger


def match_title(title: str, candidates: Iterable[Candidate]) -> NameMatch:
    """Find the catalog place a title names.

    Args:
        title: The saved place's title.
        candidates: Places of the trip's city.

    Returns:
        ``matched`` with the place, ``ambiguous`` when several fit equally well,
        else ``not_in_catalog``.
    """
    wanted = normalise(title)
    words = frozenset(wanted.split())
    exact: set[UUID] = set()
    partial: set[UUID] = set()
    for candidate in candidates:
        name = normalise(candidate.name)
        if name == wanted:
            exact.add(candidate.place_id)
        elif _contains(words, frozenset(name.split())):
            partial.add(candidate.place_id)
    found = exact or partial
    if len(found) == 1:
        return NameMatch(MatchOutcome.MATCHED, next(iter(found)))
    if found:
        return NameMatch(MatchOutcome.AMBIGUOUS)
    return NameMatch(MatchOutcome.NOT_IN_CATALOG)
