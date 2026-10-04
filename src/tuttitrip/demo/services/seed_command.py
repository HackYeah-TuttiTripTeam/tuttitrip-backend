"""Management command: reset the demo account's data.

Run ``python -m tuttitrip.demo.services.seed_command`` (the deploy does; the
worker's daily schedule calls the internal endpoint, which shares the logic in
``reset_service``). It is idempotent. Exit code 0 on success,
0 with a message when the demo is switched off, 1 on failure.
"""

import asyncio
import logging
import sys

from tuttitrip.demo.services import auth0_login, reset_service
from tuttitrip.shared.config.settings import Settings, get_settings
from tuttitrip.shared.db.session import dispose_engine

log = logging.getLogger("tuttitrip.demo.seed")


async def run(settings: Settings | None = None) -> int:
    """Reset the demo data.

    Args:
        settings: Settings override (tests); defaults to the environment.

    Returns:
        The process exit code.
    """
    settings = settings or get_settings()
    try:
        if await reset_service.reset_demo(settings) is None:
            log.info("Demo is switched off (empty TUTTITRIP_DEMO__TOKEN_SHA256)")
    except auth0_login.DemoLoginError as exc:
        log.error("Demo reset failed: %s", exc)  # ruff: ignore[error-instead-of-exception]  # no traceback: it may echo credentials
        return 1
    except Exception:
        log.exception("Demo reset failed")
        return 1
    finally:
        await dispose_engine()
    return 0


def main() -> None:
    """Entry point of ``python -m``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
