"""Match names written in free text to the people on a trip (no I/O)."""

import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from uuid import UUID

MIN_STEM = 3
MAX_ENDING = 2
LOW_CONFIDENCE = 0.6
POLISH_FOLD = str.maketrans({"ł": "l", "Ł": "L"})


@dataclass(frozen=True, slots=True)
class NameMatch:
    """Who a written name stands for: one profile, none or several candidates."""

    profile_id: UUID | None
    candidates: tuple[UUID, ...] = ()


def normalize(name: str) -> str:
    """Lowercase, drop diacritics (``ł`` too) and surrounding space.

    Args:
        name: A name as typed.

    Returns:
        ``"Kasię "`` becomes ``"kasie"``.
    """
    plain = unicodedata.normalize("NFKD", name.translate(POLISH_FOLD))
    return "".join(c for c in plain if not unicodedata.combining(c)).casefold().strip()


def _same_stem(a: str, b: str) -> bool:
    # Declined forms share a stem and differ by an ending of 2 letters at most.
    common = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        common += 1
    return (
        common >= MIN_STEM
        and len(a) - common <= MAX_ENDING
        and len(b) - common <= MAX_ENDING
    )


def match_name(name: str, people: Mapping[UUID, str]) -> NameMatch:
    """Find the person a name stands for, also in a declined form.

    An exact match (after normalising) wins; otherwise names with the same stem
    ("Kasię" for "Kasia") count, and more than one of them is ambiguous.

    Args:
        name: Name from the text.
        people: Trip profiles: id to display name.

    Returns:
        The single match, or no profile with the candidates when unknown (empty)
        or ambiguous (several).
    """
    wanted = normalize(name)
    normalized = {pid: normalize(display) for pid, display in people.items()}
    exact = sorted(
        (p for p, n in normalized.items() if n == wanted), key=lambda p: p.int
    )
    found = exact or sorted(
        (p for p, n in normalized.items() if _same_stem(wanted, n)),
        key=lambda p: p.int,
    )
    if len(found) == 1:
        return NameMatch(found[0])
    return NameMatch(None, tuple(found))


@dataclass(frozen=True, slots=True)
class DraftIssue:
    """Something the person must settle by hand."""

    code: str
    name: str | None = None
    candidates: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftPeople:
    """Payer and participants worked out from the names of a text."""

    payer: UUID | None
    participants: list[UUID]
    issues: list[DraftIssue]


def resolve_people(  # ruff: ignore[too-many-arguments] one rule set, explicit inputs
    *,
    payer_name: str | None,
    included: Collection[str],
    excluded: Collection[str],
    people: Mapping[UUID, str],
    caller: UUID | None,
    confidence: float | None,
) -> DraftPeople:
    """Choose payer and participants; list what needs a human decision.

    Without a payer name the caller (their profile) pays. Participants are the
    named ones, or everybody except the excluded when nobody is named.

    Args:
        payer_name: Who paid, as written.
        included: Names that took part (empty: everybody).
        excluded: Names that did not.
        people: Trip profiles: id to display name.
        caller: The caller's own profile, if they have one.
        confidence: The reader's confidence, if it gave one.

    Returns:
        The payer (None if unknown), participants and the issues.
    """
    issues: list[DraftIssue] = []

    def resolve(name: str) -> UUID | None:
        match = match_name(name, people)
        if match.profile_id is None:
            code = "name_ambiguous" if match.candidates else "name_not_on_trip"
            issues.append(DraftIssue(code, name, match.candidates))
        return match.profile_id

    payer = resolve(payer_name) if payer_name else caller
    if payer is None and not payer_name:
        issues.append(DraftIssue("payer_missing"))
    named = [p for n in included if (p := resolve(n)) is not None]
    out = {p for n in excluded if (p := resolve(n)) is not None}
    pool = named or [p for p in sorted(people, key=lambda p: p.int) if p not in out]
    if not pool:
        issues.append(DraftIssue("participants_empty"))
        pool = sorted(people, key=lambda p: p.int)
    if confidence is not None and confidence < LOW_CONFIDENCE:
        issues.append(DraftIssue("low_confidence"))
    return DraftPeople(payer, list(dict.fromkeys(pool)), issues)
