"""The three numbers for the jury, measured (backend#76). Local, not CI.

Run on the environment whose numbers you want to quote (develop, not a laptop):

    TUTTITRIP_MEASURE=1 uv run pytest -m integration -s \\
        tests/integration/test_jury_numbers.py

1. Violations of a chatbot's plan against the plan TuttiTrip computed, with the
   rules that fired (the linter of backend#66 on both).
2. Repeatability: 10 runs of the family plan in this process and one run each in
   two fresh processes with different ``PYTHONHASHSEED``, all with the same
   ``plan_hash`` (SHA-256, 12 characters); ``min r`` and Jain's ``r`` for context.
3. Time from one sentence to a first plan: the interview agent on a test model
   (no network) plus the plan. The real-model time is measured the same way with
   ``TUTTITRIP_MEASURE_MODEL=1`` (median of 5).

The result is a Markdown table printed at the end and, when
``TUTTITRIP_MEASURE_OUT`` names a file, written there for the comment on
HackYeah-TuttiTripTeam/tuttitrip#8.
"""

import asyncio
import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] - runs our own interpreter on a fixed script
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from pydantic_ai.models.test import TestModel

from tests.fixtures.city import places
from tests.fixtures.personas import reference_family
from tests.fixtures.planning import planning_input
from tests.fixtures.scenarios import reference
from tests.shared.db_seed import add_person, seed_city, unseed_city
from tests.shared.fakes import authorize
from tuttitrip.interview.services.interview_agent import interview_agent
from tuttitrip.main import create_app
from tuttitrip.planning.linter.schemas import LintReport, NamedPlan
from tuttitrip.planning.linter.services import trip_lint_service
from tuttitrip.planning.logic.budget_consent import plan_with_consent
from tuttitrip.planning.logic.params import DEFAULT_PARAMS
from tuttitrip.planning.plans.services import plan_service
from tuttitrip.shared.auth.schemas import AuthenticatedUser
from tuttitrip.shared.db.session import dispose_engine, get_engine, get_sessionmaker
from tuttitrip.trips.schemas import TripRole
from tuttitrip.trips.services import trip_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TUTTITRIP_MEASURE"), reason="set TUTTITRIP_MEASURE=1"
    ),
]

HOST = AuthenticatedUser(sub=f"auth0|measure-{uuid.uuid4()}")
RUNS = 10
HASH_SEEDS = ("1", "2")
SENTENCE = "Jedziemy w czwórkę, z dziećmi i babcią, na trzy dni za 1500 zł."
ROOT = Path(__file__).resolve().parents[2]
CHILD = (
    "from tests.fixtures.planning import planning_input\n"
    "from tests.fixtures.scenarios import reference\n"
    "from tuttitrip.planning.logic.params import DEFAULT_PARAMS\n"
    "from tuttitrip.planning.logic.budget_consent import plan_with_consent\n"
    "d = planning_input(reference(), lodging=False)\n"
    "print(plan_with_consent(d, DEFAULT_PARAMS, alpha=1.0).chosen.plan.plan_hash)\n"
)

# A chatbot's plan for the fixture city, written the way a chatbot writes it:
# cramming a day, a dinner at a closed hour, a place that is not in the city.
CHATBOT = NamedPlan.model_validate(
    {
        "days": [
            {
                "day": "2026-10-09",
                "items": [
                    {
                        "name": "Centrum Nauki Hevelianum",
                        "start": "09:00",
                        "end": "13:00",
                    },
                    {"name": "Wieża widokowa", "start": "13:05", "end": "14:00"},
                    {"name": "Ogród zoologiczny", "start": "14:05", "end": "18:30"},
                    {"name": "Podwodny Park Wodny", "start": "18:35", "end": "20:00"},
                ],
            },
            {
                "day": "2026-10-10",
                "items": [
                    {"name": "Park Oliwski", "start": "07:00", "end": "09:00"},
                    {"name": "Muzeum Miejskie", "start": "09:05", "end": "13:00"},
                    {"name": "Półwysep Wschodni", "start": "13:05", "end": "18:00"},
                ],
            },
        ]
    }
)


@dataclass(frozen=True)
class Measured:
    """What one run of the script found."""

    chatbot: LintReport
    tuttitrip: LintReport
    hashes: list[str]
    child_hashes: list[str]
    min_r: float
    jain: float
    interview_ms: float
    plan_ms: float


def _rules(report: LintReport) -> str:
    fired = [f"{r.rule} ({r.count})" for r in report.results if r.count]
    return ", ".join(fired) or "none"


def _child_hash(seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - our own interpreter and a fixed script
        [sys.executable, "-c", CHILD],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


async def _timed[T](call: Callable[[], Awaitable[T]]) -> tuple[T, float]:
    started = time.perf_counter()
    result = await call()
    return result, (time.perf_counter() - started) * 1000


async def _interview() -> None:
    with interview_agent.override(model=TestModel()):
        await interview_agent.run(SENTENCE)


async def _measure(app_client: httpx.AsyncClient) -> Measured:
    scenario = reference()
    trip = await app_client.post(
        "/api/v1/trips",
        json=scenario.trip.model_dump(mode="json", exclude_none=True),
    )
    assert trip.status_code == 201, trip.text
    trip_id = uuid.UUID(trip.json()["id"])
    base = f"/api/v1/trips/{trip_id}"
    try:
        for persona in reference_family().people:
            await add_person(app_client, base, persona)
        _, interview_ms = await _timed(_interview)
        started = time.perf_counter()
        plan = await app_client.post(f"{base}/plans")
        plan_ms = (time.perf_counter() - started) * 1000
        assert plan.status_code == 201, plan.text
        body = plan.json()
        async with get_sessionmaker()() as session:
            membership = await trip_service.get_membership(
                session, trip_id, HOST.sub, TripRole.HOST
            )
            ours = await trip_lint_service.lint_latest_plan(session, membership)
            theirs = await trip_lint_service.lint_named_plan(
                session, membership, CHATBOT
            )
            planning, _, _ = await plan_service.gather_input(session, membership)
        hashes = [
            (
                await asyncio.to_thread(plan_with_consent, planning, DEFAULT_PARAMS)
            ).chosen.plan.plan_hash
            for _ in range(RUNS)
        ]
        return Measured(
            chatbot=theirs,
            tuttitrip=ours,
            hashes=[body["plan_hash"], *hashes],
            child_hashes=[_child_hash(seed) for seed in HASH_SEEDS],
            min_r=body["fairness"]["min_r"],
            jain=body["fairness"]["jain"],
            interview_ms=interview_ms,
            plan_ms=plan_ms,
        )
    finally:
        await app_client.delete(base)


def _table(m: Measured) -> str:
    same = len({*m.hashes, *m.child_hashes}) == 1
    total = (m.interview_ms + m.plan_ms) / 1000
    runs = f"{len(m.hashes)} uruchomień, {len(m.child_hashes)} procesy z innym seedem"
    rows = [
        ("Naruszenia planu z czatbota", f"{m.chatbot.count} ({_rules(m.chatbot)})"),
        ("Naruszenia planu TuttiTrip", f"{m.tuttitrip.count} ({_rules(m.tuttitrip)})"),
        (
            f"Powtarzalność `plan_hash` ({runs})",
            f"{'identyczny' if same else 'RÓŻNY'}: {m.hashes[0]}",
        ),
        ("`min r` / Jain(r), liczby kontekstowe", f"{m.min_r:.3f} / {m.jain:.3f}"),
        (
            "Od zdania do planu wstępnego (wywiad na modelu testowym + plan)",
            f"{total:.2f} s (wywiad {m.interview_ms:.0f} ms, plan {m.plan_ms:.0f} ms)",
        ),
    ]
    return "\n".join(
        ["| Liczba | Wynik |", "| --- | --- |", *(f"| {a} | {b} |" for a, b in rows)]
    )


async def _run() -> Measured:
    app = create_app()
    authorize(app, HOST)
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        await seed_city()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as http:
                return await _measure(http)
        finally:
            await unseed_city()
    finally:
        await dispose_engine()


def test_the_three_numbers() -> None:
    measured = asyncio.run(_run())
    table = _table(measured)
    print(f"\n{table}\n")  # ruff: ignore[print] - the script's output
    out = os.environ.get("TUTTITRIP_MEASURE_OUT")
    if out:
        Path(out).write_text(table + "\n", encoding="utf-8")
    assert len(set(measured.hashes) | set(measured.child_hashes)) == 1
    assert measured.chatbot.count > measured.tuttitrip.count
    assert places()  # the catalog the numbers are taken on is the fixture city
    assert planning_input(reference(), lodging=False)
