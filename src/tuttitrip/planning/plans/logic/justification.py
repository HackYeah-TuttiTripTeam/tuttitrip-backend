"""Justification of a verdict from its data: the template used until a model writes one.

Pure. The text only restates what the verdict already says (the counts of
people for and against and the E0 codes), so it never claims more than the
algorithm decided. The worker's model text replaces it when ready
(``write_justifications``).
"""

from typing import Final

from tuttitrip.planning.plans.schemas import VerdictKind
from tuttitrip.shared.jobs.contracts import Locale

_MUST: Final = {
    "pl": "Host wymusił to miejsce.",
    "en": "The host made this place a must.",
}
_FITS: Final = {
    "pl": "Pasuje grupie: {yes} na tak, {no} na nie.",
    "en": "Fits the group: {yes} for, {no} against.",
}
_ICONIC: Final = {
    "pl": "Znane miejsce, ale poza gustem grupy: {yes} na tak, {no} na nie.",
    "en": "A well-known place, but not the group's taste: {yes} for, {no} against.",
}
_SKIP_NO_CODE: Final = {
    "pl": "Poza planem: pasuje mniej niż wybrane ({yes} na tak, {no} na nie).",
    "en": "Left out: suits the group less than the chosen ({yes} for, {no} against).",
}
_SKIP: Final = {"pl": "Odpada: {reasons}.", "en": "Ruled out: {reasons}."}
_CODES: Final = {
    "pl": {
        "veto": "ktoś zgłosił weto",
        "blocked": "host je zablokował",
        "closed": "zamknięte w dniach wyjazdu",
        "no_fit": "nie mieści się w oknie dnia",
        "segment": "za daleko dla kogoś z grupy",
        "stairs": "schody, których ktoś nie pokona",
    },
    "en": {
        "veto": "someone vetoed it",
        "blocked": "the host blocked it",
        "closed": "closed on the trip days",
        "no_fit": "does not fit the day window",
        "segment": "too far for someone in the group",
        "stairs": "stairs someone cannot take",
    },
}


def template_justification(
    kind: VerdictKind, yes: int, no: int, skip_codes: list[str], locale: Locale
) -> str:
    """Sentence for a verdict.

    Args:
        kind: The verdict.
        yes: People for the place.
        no: People against it.
        skip_codes: E0 codes of a skip; unknown codes are left out.
        locale: ``pl`` or ``en``.

    Returns:
        The text.
    """
    if kind is VerdictKind.MUST:
        return _MUST[locale]
    if kind is VerdictKind.FITS:
        return _FITS[locale].format(yes=yes, no=no)
    if kind is VerdictKind.ICONIC_NOT_YOURS:
        return _ICONIC[locale].format(yes=yes, no=no)
    names = [_CODES[locale][c] for c in skip_codes if c in _CODES[locale]]
    if names:
        return _SKIP[locale].format(reasons=", ".join(names))
    return _SKIP_NO_CODE[locale].format(yes=yes, no=no)
