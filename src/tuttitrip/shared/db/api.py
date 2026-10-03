"""FastAPI dependency that hands a database session to a request."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tuttitrip.shared.db.session import get_sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Open a session for the duration of one request.

    Yields:
        An async session; services decide when to commit.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
