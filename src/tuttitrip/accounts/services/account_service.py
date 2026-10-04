"""Change the caller's own Auth0 account through the Management API."""

from tuttitrip.accounts.logic.sources import account_source, is_editable
from tuttitrip.accounts.schemas import AccountRead, AccountSource, AccountUpdate
from tuttitrip.shared.admin_users.services.management_client import ManagementClient


class ProviderManagedAccountError(Exception):
    """The account's data belongs to a social provider (Google, Discord...)."""

    def __init__(self, source: AccountSource) -> None:
        """Keep the provider.

        Args:
            source: Provider that owns the data.
        """
        super().__init__(f"Account data comes from {source}")
        self.source = source


class ManagementNotConfiguredError(Exception):
    """The Management API credentials are not set."""


async def update_account(
    client: ManagementClient, sub: str, data: AccountUpdate
) -> AccountRead:
    """Rename the account of ``sub`` (always the token's account).

    The new name reaches the access and ID tokens only after the next login.

    Args:
        client: Management API client.
        sub: Auth0 user id from the verified access token.
        data: The new name.

    Returns:
        The account as Auth0 stored it.

    Raises:
        ProviderManagedAccountError: Google, Discord or another social account.
        ManagementNotConfiguredError: No Management API credentials.
    """
    source = account_source(sub)
    if not is_editable(source):
        raise ProviderManagedAccountError(source)
    if not client.configured:
        raise ManagementNotConfiguredError
    user = await client.update_user(sub, {"name": data.name})
    return AccountRead(
        sub=sub, name=user.get("name"), email=user.get("email"), source=source
    )
