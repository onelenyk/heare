"""The scenarios, as tests.

These drive the assembled daemon — every stage, the real model, real
speech recognition, real synthesis — through a simulated room. They take
minutes and they cost money, so they are excluded from the default run
and asked for by name:

    make e2e
    pytest -m e2e
    pytest -m e2e -k delegate

They exist because the failures this project has actually had did not
live in any unit: an echo canceller that deleted every sample, a debounce
that held finished words for three seconds, a prompt that ordered a tool
the agent could not call. Each of those passed every test in the suite
while the assistant was unusable.

Each run gets a throwaway database, so the daemon can stay up and the
real conversation history stays out of it. That was not cosmetic: while
scenarios shared the live database they contended for its write lock,
inherited days of context, and went three different ways on three
identical runs.
"""

from __future__ import annotations

import asyncio

import pytest

from src.pipeline.room import SCENARIOS, Room, record_run
from src.pipeline.checks import run_checks

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(name: str) -> None:
    scenario = SCENARIOS[name]
    room = Room()
    result = asyncio.run(room.run(scenario.script, timeout=scenario.window))

    record_run(scenario, room, result)

    assert result.frames_fed > 0, (
        "no audio reached the pipeline — the harness broke, not the assistant"
    )

    failures = run_checks(scenario.checks, result)
    assert not failures, (
        f"{scenario.name} — {scenario.expect}\n  "
        + "\n  ".join(failures)
        + f"\n\nheard: {result.heard}\nsaid: {result.spoken}"
    )
