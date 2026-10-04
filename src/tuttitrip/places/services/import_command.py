"""Management command: load the cities sheet into the catalog.

``python -m tuttitrip.places.services.import_command [FILE] [--check]``

FILE defaults to ``miasta.xlsx`` in ``TUTTITRIP_CITIES__DATA_DIR`` (the
``tuttitrip-cities-data`` volume the deploy fills). ``--check`` validates the
workbook only, without a database. Exit codes: 0 imported, 1 the workbook or
the import failed (nothing was written), 2 there is no file to import.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from tuttitrip.places.logic.sheet_rows import SheetImportError
from tuttitrip.places.services import sheet_import_service
from tuttitrip.places.services.sheet_import_service import CityCounts
from tuttitrip.shared.config.settings import get_settings
from tuttitrip.shared.db.session import dispose_engine, get_sessionmaker

log = logging.getLogger("tuttitrip.places.import")

SHEET_FILE = "miasta.xlsx"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_FILE = 2


def _log_counts(counts: dict[str, CityCounts]) -> None:
    for slug, c in counts.items():
        log.info(
            "%s: %d places, %d lodgings, %d fares, %d prices"
            " (%d without hours, %d without prices)",
            slug,
            c.places,
            c.lodgings,
            c.fares,
            c.prices,
            c.unknown_hours,
            c.unpriced,
        )


async def _import(path: Path, *, check_only: bool) -> int:
    """Run the import (or the check) and log the result.

    Args:
        path: The XLSX file.
        check_only: Only validate, do not touch the database.

    Returns:
        The process exit code.
    """
    if check_only:
        catalog = sheet_import_service.read_catalog(path)
        _log_counts(sheet_import_service.summarize(catalog))
        for warning in catalog.warnings:
            log.warning("%s", warning)
        return EXIT_OK
    async with get_sessionmaker()() as session:
        report = await sheet_import_service.import_sheet(session, path)
    _log_counts(report.cities)
    log.info(
        "inserted %d, updated %d, taken over from OSM %d, in the catalog but "
        "no longer in the sheet %d",
        report.inserted,
        report.updated,
        report.taken_from_osm,
        report.orphans,
    )
    for warning in report.warnings:
        log.warning("%s", warning)
    return EXIT_OK


async def run(path: Path, *, check_only: bool = False) -> int:
    """Validate (and unless ``check_only``, import) the workbook.

    Args:
        path: The XLSX file.
        check_only: Only validate, do not touch the database.

    Returns:
        The process exit code.
    """
    try:
        return await _import(path, check_only=check_only)
    except SheetImportError as exc:
        log.error("%s", exc)  # ruff: ignore[error-instead-of-exception]  # a data error, a traceback adds nothing
        return EXIT_FAILED
    except Exception:
        log.exception("City import failed, nothing was written")
        return EXIT_FAILED
    finally:
        await dispose_engine()


def main() -> None:
    """Entry point of ``python -m``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--check", action="store_true", help="validate only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    path = args.file or get_settings().cities.data_dir / SHEET_FILE
    if not path.is_file():
        log.error("No city sheet at %s", path)
        sys.exit(EXIT_NO_FILE)
    sys.exit(asyncio.run(run(path, check_only=args.check)))


if __name__ == "__main__":
    main()
