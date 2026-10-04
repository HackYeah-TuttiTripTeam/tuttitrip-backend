"""Fixed values of the permissions admin API."""

from typing import Final

AUDIT_DEFAULT_LIMIT: Final = 100
"""Entries of the audit log returned when the caller does not say."""

AUDIT_MAX_LIMIT: Final = 500
"""Largest ``limit`` the audit log endpoint accepts."""
