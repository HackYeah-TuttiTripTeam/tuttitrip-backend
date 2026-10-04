"""An in-memory trip for the interview tools: real services over a fake database.

The ``trips``, ``profiles`` and ``interview`` services run for real, so age
defaults, trip rules and the digests behind "who set this value" are the
production ones. Only the table access (``db`` modules) and the preferences
service are replaced.
"""

import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

from tuttitrip.interview import db as interview_db
from tuttitrip.interview.schemas import FieldRef
from tuttitrip.interview.services.interview_deps import InterviewDeps
from tuttitrip.profiles import db as profile_db
from tuttitrip.profiles.models import Profile
from tuttitrip.profiles.preferences.logic.importance import default_pool
from tuttitrip.profiles.preferences.schemas import (
    ImportancePool,
    PreferencesRead,
    PreferencesWrite,
)
from tuttitrip.profiles.preferences.services import preference_service
from tuttitrip.profiles.services import profile_service
from tuttitrip.trips.schemas import (
    TripMembership,
    TripRead,
    TripRole,
    TripUpdate,
    check_trip,
)
from tuttitrip.trips.services import trip_service
from tuttitrip.trips.services.trip_service import TripInvalidError

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class World:
    """One trip with its people, preferences and assistant digests."""

    def __init__(self, role: TripRole = TripRole.HOST) -> None:
        self.trip_id = uuid.uuid4()
        self.membership = TripMembership(
            trip_id=self.trip_id, sub="auth0|host", role=role
        )
        self.trip = TripRead(
            id=self.trip_id,
            name="Weekend",
            destination=None,
            created_at=NOW,
            start_date=None,
            end_date=None,
            day_start="09:00",
            day_end="19:00",
            city_slug=None,
            currency=None,
            budget_total_min=None,
            budget_total_max=None,
            budget_day_min=None,
            budget_day_max=None,
            budget_flex_pct=10,
            fairness_alpha=1.0,
            my_role=role,
        )
        self.profiles: dict[uuid.UUID, Profile] = {}
        self.saved: dict[uuid.UUID, PreferencesWrite] = {}
        self.digests: dict[FieldRef, str] = {}
        self.writes = 0
        self.running: dict[uuid.UUID, tuple[datetime, float]] = {}
        self.voice_used: dict[uuid.UUID, int] = {}
        self.known_sessions: set[uuid.UUID] | None = None
        self._claims = 0
        self.session = MagicMock()
        self.session.commit = AsyncMock()
        self.session.rollback = AsyncMock()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the table access and the preferences service.

        Args:
            monkeypatch: The test's monkeypatch.
        """
        monkeypatch.setattr(trip_service, "get_trip", self._get_trip)
        monkeypatch.setattr(trip_service, "update_trip", self._update_trip)
        monkeypatch.setattr(profile_db, "select_profiles_by_trip", self._profiles)
        monkeypatch.setattr(profile_db, "select_profile", self._profile)
        monkeypatch.setattr(profile_db, "insert_profile", self._insert_profile)
        monkeypatch.setattr(preference_service, "list_preferences", self._list_prefs)
        monkeypatch.setattr(preference_service, "get_preferences", self._get_prefs)
        monkeypatch.setattr(
            preference_service, "replace_preferences", self._replace_prefs
        )
        monkeypatch.setattr(interview_db, "select_assistant_digests", self._digests)
        monkeypatch.setattr(interview_db, "upsert_assistant_digest", self._upsert)
        monkeypatch.setattr(interview_db, "try_start_run", self._try_start)
        monkeypatch.setattr(interview_db, "end_run", self._end_run)
        monkeypatch.setattr(interview_db, "select_session", self._select_session)

    def deps(self) -> InterviewDeps:
        """Deps whose sessions are the fake one.

        Returns:
            Fresh dependencies for one turn.
        """

        @asynccontextmanager
        async def sessions() -> AsyncGenerator[Any]:
            yield self.session

        return InterviewDeps(
            membership=self.membership,
            session_id=uuid.uuid4(),
            sessions=sessions,  # ty: ignore[invalid-argument-type]
        )

    async def add_host(self) -> Profile:
        """Create the host's own profile, as ``POST /trips`` does.

        Returns:
            The stored profile.
        """
        await profile_service.create_account_profile(
            self.session, self.trip_id, self.membership.sub, "Organizator"
        )
        return next(iter(self.profiles.values()))

    # --- trips ---------------------------------------------------------------

    async def _get_trip(self, _s: object, _m: object) -> TripRead:
        return self.trip

    async def _update_trip(self, _s: object, _m: object, data: TripUpdate) -> TripRead:
        changes = data.model_dump(exclude_unset=True)
        merged = TripUpdate.model_validate(self.trip, from_attributes=True).model_copy(
            update=changes
        )
        try:
            check_trip(merged, complete=True)
        except ValidationError as exc:
            raise TripInvalidError(
                [
                    dict(e)
                    for e in exc.errors(
                        include_url=False, include_input=False, include_context=False
                    )
                ]
            ) from exc
        self.writes += 1
        self.trip = self.trip.model_copy(update=changes)
        return self.trip

    # --- profiles ------------------------------------------------------------

    async def _profiles(self, _s: object, _t: object) -> list[Profile]:
        return list(self.profiles.values())

    async def _profile(self, _s: object, _t: object, pid: uuid.UUID) -> Profile | None:
        return self.profiles.get(pid)

    async def _insert_profile(self, _s: object, profile: Profile) -> Profile:
        profile.id = profile.id or uuid.uuid4()
        profile.trip_id = self.trip_id
        profile.weight = profile.weight or 1.0
        profile.nap_minutes = profile.nap_minutes or 0
        self.profiles[profile.id] = profile
        self.writes += 1
        return profile

    # --- preferences ---------------------------------------------------------

    def _prefs(self, profile: Profile) -> PreferencesRead:
        saved = self.saved.get(profile.id)
        write = saved or PreferencesWrite()
        pool = write.importance_pool or ImportancePool.model_validate(
            {d.value: p for d, p in default_pool(profile.age_group).items()}
        )
        return PreferencesRead(
            profile_id=profile.id,
            interests=write.interests,
            diet=write.diet,
            example_places=write.example_places,
            min_tags=write.min_tags,
            importance_pool=pool,
            constraints=write.constraints,
            effective_stairs_sensitivity=profile.stairs_sensitivity,
            filled=saved is not None,
            updated_by_sub=None,
            updated_at=None,
        )

    async def _list_prefs(self, _s: object, _m: object) -> list[PreferencesRead]:
        return [self._prefs(p) for p in self.profiles.values()]

    async def _get_prefs(
        self, _s: object, _m: object, pid: uuid.UUID
    ) -> PreferencesRead:
        profile = self.profiles.get(pid)
        if profile is None:
            raise profile_service.ProfileNotFoundError(str(pid))
        return self._prefs(profile)

    async def _replace_prefs(
        self, _s: object, _m: object, pid: uuid.UUID, data: PreferencesWrite
    ) -> PreferencesRead:
        self.writes += 1
        self.saved[pid] = data
        return self._prefs(self.profiles[pid])

    # --- digests -------------------------------------------------------------

    async def _digests(self, _s: object, _t: object) -> dict[FieldRef, str]:
        return dict(self.digests)

    async def _upsert(self, _s: object, _t: object, ref: FieldRef, digest: str) -> None:
        self.digests[ref] = digest

    # --- run guard (the atomic UPDATE of ``interview.db``, in memory) ---------

    async def _try_start(
        self,
        _s: object,
        trip_id: uuid.UUID,
        session_id: uuid.UUID,
        ttl: float,
        voice_limit: int | None,
    ) -> datetime | None:
        if trip_id != self.trip_id or not self._exists(session_id):
            return None
        held = self.running.get(session_id)
        if held is not None and held[1] > time.monotonic():
            return None
        used = self.voice_used.get(session_id, 0)
        if voice_limit is not None and used >= voice_limit:
            return None
        self._claims += 1
        token = NOW + timedelta(seconds=self._claims)
        self.running[session_id] = (token, time.monotonic() + ttl)
        return token

    async def _end_run(
        self, _s: object, session_id: uuid.UUID, token: datetime, voice_seconds: int
    ) -> None:
        held = self.running.get(session_id)
        if held is not None and held[0] == token:
            del self.running[session_id]
        if voice_seconds:
            used = self.voice_used.get(session_id, 0)
            self.voice_used[session_id] = used + voice_seconds

    async def _select_session(
        self, _s: object, trip_id: uuid.UUID, session_id: uuid.UUID, **_kw: object
    ) -> SimpleNamespace | None:
        if trip_id != self.trip_id or not self._exists(session_id):
            return None
        return SimpleNamespace(voice_seconds=self.voice_used.get(session_id, 0))

    def _exists(self, session_id: uuid.UUID) -> bool:
        return self.known_sessions is None or session_id in self.known_sessions


def model_of(
    respond: Callable[
        [list[ModelMessage], AgentInfo], ModelResponse | Awaitable[ModelResponse]
    ],
    name: str = "function",
) -> FunctionModel:
    """A ``FunctionModel`` that also answers streamed requests (the AG-UI run).

    Args:
        respond: Returns the whole response (sync or async).
        name: The model name the responses carry.

    Returns:
        The model.
    """

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        response = respond(messages, info)
        if isinstance(response, Awaitable):
            response = await response
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=part.args_as_json_str(),
                        tool_call_id=part.tool_call_id,
                    )
                }

    return FunctionModel(respond, stream_function=stream, model_name=name)
