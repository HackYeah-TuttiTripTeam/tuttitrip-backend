"""What every tool of the interview agent gets: the caller and the database."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tuttitrip.interview.schemas import InterviewState
from tuttitrip.trips.schemas import TripMembership


@dataclass
class InterviewDeps:
    """Per-turn dependencies: membership and ids come from the server, not the model.

    The tools open their own sessions: the request's session is closed when the
    endpoint returns, long before the stream ends. ``voice`` hides the card tool.
    ``state`` satisfies the AG-UI ``StateHandler`` protocol and is what
    ``STATE_SNAPSHOT`` carries.
    """

    membership: TripMembership
    session_id: UUID
    sessions: async_sessionmaker[AsyncSession]
    state: InterviewState = field(default_factory=InterviewState)
    voice: bool = False
