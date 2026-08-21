"""A role has to be able to say no, on the path the work actually takes.

These assertions used to run against the old engine's handler wrapper.
That wrapper went with the engine, and the worker never passed through
it — so the same guarantees are made in ``Hands._execute``, and this is
where they have to be checked. Left pointing at the old wrapper, they
would have kept passing against code nothing calls while every
restriction quietly became "allow everything".

They used to be written in terms of modes. The modes are gone — a global
adjective every layer had to remember to consult, which by the end no
layer did. What is being asked here never changed: while something is in
force, does the tool it forbids actually fail to run. Only the thing in
force is different, and this one has a lifetime, so there is one object
to ask.
"""

from __future__ import annotations

import asyncio

from src.agent.hands import Hands
from src.agent.tool_gate import OPEN, ToolPolicy
from src.config import Settings

# What the меeting role denies, straight out of roles/meeting.md.
MEETING = ToolPolicy(
    name="роль «мітинг»",
    denied_tool_patterns=("bash", "write", "stop_daemon", "restart_daemon", "mcp__*"),
    voice_muted=True,
)


class _SessionState:
    """All ``Hands`` asks of a session state is ``.policy``.

    It used to be handed the pipeline's ``SessionState``, which carried a
    language, a flush hook and mode-change listeners besides. That class
    is deleted, and the object the running spine passes here is its own
    duck type with exactly this one property (``_RoleSessionState`` in
    src/spine/main.py). Matching that shape is what keeps these tests
    honest about the gate the work really goes through.
    """

    def __init__(self, policy: ToolPolicy) -> None:
        self.policy = policy


def _hands(policy: ToolPolicy) -> Hands:
    return Hands(Settings(), session_state=_SessionState(policy))


def _run(hands: Hands, tool: str, args: dict) -> str:
    return asyncio.run(hands._execute(tool, args))


def test_a_permitted_tool_runs(monkeypatch) -> None:
    reached = {"v": False}

    async def _fake_execute_direct(*a, **k):
        reached["v"] = True
        return {"success": True, "output": "ran"}

    monkeypatch.setattr(
        "src.agent.tools.direct.execute_direct", _fake_execute_direct
    )

    assert _run(_hands(OPEN), "bash", {"command": "ls"}) == "ran"
    assert reached["v"] is True


def test_a_denied_tool_never_reaches_the_shell(monkeypatch) -> None:
    """Refusing after running is not refusing."""
    reached = {"v": False}

    async def _fake_execute_direct(*a, **k):
        reached["v"] = True
        return {"success": True, "output": "ran"}

    monkeypatch.setattr(
        "src.agent.tools.direct.execute_direct", _fake_execute_direct
    )

    out = _run(_hands(MEETING), "bash", {"command": "ls"})

    assert reached["v"] is False
    assert "refused" in out
    assert "мітинг" in out, "it should say what was in force, not just that it said no"


def test_a_glob_denies_the_whole_family(monkeypatch) -> None:
    """`mcp__*` is one line in a role file and an unknown number of tools
    at runtime — servers connect after the role was written."""
    reached = {"v": False}

    async def _fake_execute_direct(*a, **k):
        reached["v"] = True
        return {"success": True, "output": "ran"}

    monkeypatch.setattr(
        "src.agent.tools.direct.execute_direct", _fake_execute_direct
    )

    out = _run(_hands(MEETING), "mcp__files__write_file", {"path": "/tmp/x"})

    assert reached["v"] is False
    assert "refused" in out


def test_nothing_in_force_forbids_nothing(monkeypatch) -> None:
    """Outside a role session there is no policy to consult. The old code
    reached for the `ambient` mode profile here — a registry lookup to
    answer a question no mode had influenced in months."""
    reached = {"v": False}

    async def _fake_execute_direct(*a, **k):
        reached["v"] = True
        return {"success": True, "output": "ran"}

    monkeypatch.setattr(
        "src.agent.tools.direct.execute_direct", _fake_execute_direct
    )

    assert _run(Hands(Settings()), "bash", {"command": "ls"}) == "ran"
    assert reached["v"] is True


def test_the_schema_offered_to_the_worker_hides_what_it_may_not_run() -> None:
    """Otherwise the model announces work it will then be refused, which
    reads to the person as the assistant changing its mind."""
    names = {s["function"]["name"] for s in _hands(MEETING)._tool_schemas()}

    assert "bash" not in names
    assert "read" in names
