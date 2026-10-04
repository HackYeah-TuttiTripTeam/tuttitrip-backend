"""City slug from a free-text name: the one rule backend and worker share (pure)."""

import re
import unicodedata

_STROKED = str.maketrans({"ł": "l", "Ł": "L"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lower-case, drop diacritics (``ł`` too), turn other runs into one ``-``.

    Args:
        name: City name, e.g. ``"Gdańsk, Polska"``.

    Returns:
        The slug (``"gdansk-polska"``), empty when the name has no letters.
    """
    decomposed = unicodedata.normalize("NFKD", name.translate(_STROKED))
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub("-", ascii_name.casefold()).strip("-")
