"""A mode has to be able to say no, on the path the work actually takes.

These assertions used to run against the old engine's handler wrapper.
That wrapper is gone with the engine, and the worker never went through it —
so the same guarantees are now made in ``Hands._execute``, and this is
where they have to be checked. Left pointing at the old wrapper, the
tests would have kept passing against code nothing calls while every mode
quietly became "allow everything".
"""

from __future__ import annotations

import asyncio

from src.agent.hands import Hands
from src.agent.modes import MODE_PROFILES
from src.config import Settings


class _SessionState:
    """All ``Hands`` asks of a session state is ``.profile``.

    It used to be handed the pipeline's ``SessionState``, which carried a
    language, a flush hook and mode-change listeners besides. That class
    is deleted, and the object the running spine passes here is its own
    duck type with exactly this one property (``_RoleSessionState`` in
    src/spine/main.py). Matching that shape is what keeps these tests
    honest about the gate the work really goes through."""

    def __init__(self, mode: str) -> None:
        self.profile = MODE_PROFILES[mode]


def _hands(mode: str) -> Hands:
    return Hands(Settings(), session_state=_SessionState(mode))


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

    assert _run(_hands("ambient"), "bash", {"command": "ls"}) == "ran"
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

    out = _run(_hands("meeting"), "bash", {"command": "ls"})

    assert reached["v"] is False
    assert "refused" in out
    assert "meeting" in out


def test_switching_mode_is_never_denied(monkeypatch) -> None:
    """Otherwise a mode that denies everything cannot be left."""
    reached = {"v": False}

    async def _fake_execute_direct(*a, **k):
        reached["v"] = True
        return {"success": True, "output": "mode set"}

    monkeypatch.setattr(
        "src.agent.tools.direct.execute_direct", _fake_execute_direct
    )

    assert _run(_hands("meeting"), "set_mode", {"mode": "ambient"}) == "mode set"
    assert reached["v"] is True
