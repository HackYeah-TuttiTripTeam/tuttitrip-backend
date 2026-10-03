"""Check a demo token against the configured SHA-256."""

import hashlib
import hmac


def token_matches(token: str, expected_sha256: str) -> bool:
    """Compare the SHA-256 of ``token`` with the configured hex digest.

    Args:
        token: The token sent by the client.
        expected_sha256: Hex SHA-256 from the settings; empty disables the demo.

    Returns:
        True only for a non-empty configured digest that matches.
    """
    expected = expected_sha256.strip().lower()
    if not expected:
        return False
    actual = hashlib.sha256(token.encode()).hexdigest()
    return hmac.compare_digest(actual, expected)
