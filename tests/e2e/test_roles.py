"""A role: entered by speaking, held, left by speaking, leaves a document.

Roles replaced the modes, and the reason is here in the shape of the
tests: a role has a lifetime. It starts on a phrase, holds, ends on
another, and there is always one object to ask what is in force. A mode
had no lifetime, so every layer had to remember to consult a global flag
— and by the end none of them did.
"""

from __future__ import annotations

import pytest

from tests.e2e.room import close_room, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


async def test_a_spoken_phrase_starts_it(room) -> None:
    await room.said_to_it("Дока, почни мітинг")

    active = room.loop.role_manager.active
    assert active is not None
    assert active.name == "мітинг"


async def test_inside_a_session_the_whole_room_is_the_point(room) -> None:
    """The wake gate is bypassed in session: in a meeting, what other
    people say is the content, and requiring the assistant's name before
    every line would make it useless for the one job it has."""
    await room.said_to_it("Дока, почни мітинг")

    await room.overhears("реліз переносимо на середу")

    assert any("середу" in text for text in room.heard()), (
        "a line nobody addressed to it still belongs to the meeting"
    )


async def test_what_the_role_forbids_is_actually_refused(room) -> None:
    """The gate that survived the modes. `deny_tools` in a role file has
    to be an enforced refusal inside the worker, not a suggestion in a
    prompt — and it must name what is in force, so a refusal is
    intelligible rather than mysterious."""
    await room.said_to_it("Дока, почни мітинг")

    # The worker the app actually built, not one assembled for the test.
    refusal = await room.loop.toolbox._hands._execute(
        "bash", {"command": "echo привіт"}
    )

    assert "refused" in refusal
    assert "мітинг" in refusal


async def test_the_worker_is_never_offered_what_it_may_not_run(room) -> None:
    """Otherwise the model announces work it will then be refused, which
    reads to the person as the assistant changing its mind."""
    await room.said_to_it("Дока, почни мітинг")

    names = {
        s["function"]["name"]
        for s in room.loop.toolbox._hands._tool_schemas()
    }

    assert "bash" not in names
    assert "read" in names


async def test_a_meeting_nobody_held_gets_no_protocol(room) -> None:
    """Ending a session that heard nothing used to produce a summary of
    nothing, which is worse than no summary: a document that looks like a
    record and is not one."""
    await room.said_to_it("Дока, почни мітинг")

    await room.said_to_it("закінчили")

    assert room.loop.role_manager.active is None, "the phrase has to end it"
    assert room.loop.role_flow.finishing is False


async def test_outside_a_session_nothing_is_in_force(room) -> None:
    """The old code reached for the `ambient` mode profile here — a
    registry lookup to answer a question no mode had influenced in
    months."""
    from src.agent.tool_gate import is_tool_allowed

    policy = room.loop.toolbox._hands._session_state.policy

    assert is_tool_allowed(policy, "bash") is True
