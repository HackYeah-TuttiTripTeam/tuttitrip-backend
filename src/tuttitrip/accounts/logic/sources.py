"""Where an account's data comes from, read from its Auth0 ``sub``."""

from tuttitrip.accounts.schemas import AccountSource

_PREFIXES: tuple[tuple[str, AccountSource], ...] = (
    ("auth0|", AccountSource.EMAIL),
    ("google-oauth2|", AccountSource.GOOGLE),
    ("oauth2|discord|", AccountSource.DISCORD),
)


def account_source(sub: str) -> AccountSource:
    """Tell the login provider of an account.

    Auth0 builds the ``sub`` from the connection: ``auth0|`` for the e-mail and
    password database, ``google-oauth2|`` for Google and ``oauth2|discord|``
    for the custom Discord connection.

    Args:
        sub: Auth0 user id from the access token.

    Returns:
        The source; ``other`` for any connection not listed above.
    """
    for prefix, source in _PREFIXES:
        if sub.startswith(prefix):
            return source
    return AccountSource.OTHER


def is_editable(source: AccountSource) -> bool:
    """Tell whether TuttiTrip may change the account's data.

    Social providers own the name (Auth0 overwrites it at the next login), so
    only e-mail and password accounts are edited here.

    Args:
        source: The account's login provider.

    Returns:
        True only for e-mail and password accounts.
    """
    return source is AccountSource.EMAIL
