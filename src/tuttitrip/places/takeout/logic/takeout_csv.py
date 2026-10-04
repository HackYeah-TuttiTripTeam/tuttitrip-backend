"""Read a Google Takeout list of saved places (``Saved/<list>.csv``) (pure).

Takeout writes one CSV per list with the columns ``Title, Note, URL, Tags,
Comment`` (the Polish interface exports ``Tytuł, Notatka, Adres URL, Tagi,
Komentarz``). The file has no coordinates, so a place is told apart by its
title alone; when the title is empty the name is read from the Maps link
(``/maps/place/<name>/...``). The format is not documented by Google; it was
checked against exports of both interface languages.
"""

import csv
import io
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

MAX_ROWS: Final = 500
MAX_BYTES: Final = 1_000_000
_TITLE_HEADERS: Final = frozenset({"title", "tytuł", "tytul"})
_URL_HEADERS: Final = frozenset({"url", "adres url"})
_PLACE_PATH: Final = "/maps/place/"


class TakeoutFormatError(ValueError):
    """The file is not a Takeout list: encoding, header or size is wrong."""


@dataclass(frozen=True, slots=True)
class TakeoutRow:
    """One saved place of the list."""

    line: int
    """1-based line of the file (the header is line 1)."""
    title: str
    """The place name; empty when neither the title nor the link has one."""


def _name_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path)
    if _PLACE_PATH not in path:
        return ""
    segment = path.split(_PLACE_PATH, 1)[1].split("/", 1)[0]
    return segment.replace("+", " ").strip()


def _column(header: list[str], names: frozenset[str]) -> int | None:
    for index, cell in enumerate(header):
        if cell.strip().casefold() in names:
            return index
    return None


def parse_takeout(data: bytes) -> list[TakeoutRow]:
    """Read the saved places of a Takeout CSV.

    Args:
        data: The file as uploaded (UTF-8, with or without a byte order mark).

    Returns:
        One row per non-empty line, in file order.

    Raises:
        TakeoutFormatError: Too big, not UTF-8, no title column, no rows, or
            more than ``MAX_ROWS`` rows.
    """
    if len(data) > MAX_BYTES:
        msg = f"The file is larger than {MAX_BYTES} bytes"
        raise TakeoutFormatError(msg)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        msg = "The file is not UTF-8 text"
        raise TakeoutFormatError(msg) from exc
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    title_at = None if header is None else _column(header, _TITLE_HEADERS)
    if header is None or title_at is None:
        msg = "No Title column: this is not a Google Takeout list of saved places"
        raise TakeoutFormatError(msg)
    url_at = _column(header, _URL_HEADERS)
    rows: list[TakeoutRow] = []
    for record in reader:
        if not any(cell.strip() for cell in record):
            continue
        title = record[title_at].strip() if title_at < len(record) else ""
        if not title and url_at is not None and url_at < len(record):
            title = _name_from_url(record[url_at].strip())
        rows.append(TakeoutRow(reader.line_num, title))
    if not rows:
        msg = "The list has no places"
        raise TakeoutFormatError(msg)
    if len(rows) > MAX_ROWS:
        msg = f"The list has more than {MAX_ROWS} places"
        raise TakeoutFormatError(msg)
    return rows
