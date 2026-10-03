"""Management command: reset the demo account's data.

Run ``python -m tuttitrip.demo.services.seed_command`` (the deploy does, and
the worker's daily schedule can). It is idempotent. Exit code 0 on success,
0 with a message when the demo is switched off, 1 on failure.
"""

import asyncio
import logging
import sys

from tuttitrip.demo.services import auth0_login, demo_service
from tuttitrip.shared.auth.services.token_verifier import TokenVerifier
from tuttitrip.shared.config.settings import Settings, get_settings
from tuttitrip.shared.db.session import dispose_engine, get_engine

log = logging.getLogger("tuttitrip.demo.seed")


async def _demo_sub(settings: Settings) -> str:
    """The demo account's ``sub``, from a verified token of its own login.

    Returns:
        The Auth0 subject the credentials belong to.
    """
    async with auth0_login.build_client() as client:
        session = await auth0_login.login(client, settings.auth0, settings.demo)
    verifier = TokenVerifier(
        domain=settings.auth0.domain,
        audience=settings.auth0.audience,
        roles_claim=settings.auth0.roles_claim,
    )
    return await asyncio.to_thread(lambda: verifier.verify(session.access_token).sub)


async def run(settings: Settings | None = None) -> int:
    """Reset the demo data.

    Args:
        settings: Settings override (tests); defaults to the environment.

    Returns:
        The process exit code.
    """
    settings = settings or get_settings()
    if not settings.demo.token_sha256:
        log.info("Demo is switched off (empty TUTTITRIP_DEMO__TOKEN_SHA256)")
        return 0
    try:
        sub = await _demo_sub(settings)
        await demo_service.run_reset(get_engine(), sub)
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
