"""What every tool of the interview agent gets: the caller and the database."""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tuttitrip.interview.schemas import InterviewState, NextQuestion
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
    own_profile_id: UUID | None = None
    """Set for a member's interview: the one profile its tools may write."""
    chosen: dict[str, NextQuestion | None] = field(default_factory=dict)
    """The next question for each state of the panel, so the solver measures once."""

    @property
    def is_member(self) -> bool:
        """Whether this is a member's own interview (own data only)."""
        return self.own_profile_id is not None
