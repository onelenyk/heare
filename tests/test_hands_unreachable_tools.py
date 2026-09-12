"""The worker must not offer what nothing can carry out.

For a month the daemon handed the model schemas for tools whose other
half did not exist in this process. The sub-agents are gone now
(12 September); what remains is the browser bridge, constructed at boot
since 9 September and reachable only once a Chrome extension pairs, and
two tools that execute cleanly, write State, and return success to a
reader that does not exist in either engine.

A tool the model can see is a promise to the person on the microphone.
"""
from __future__ import annotations

from types import SimpleNamespace

from unittest.mock import MagicMock

import pytest

from src.agent.hands import (
    _BROWSER_TOOLS,
    _DYNAMIC_TOOL_VERBS,
    _TOOLS_WITH_NO_READER,
    Hands,
    unreachable_tools,
)


@pytest.fixture
def schema_names():
    def _names(settings=None):
        hands = Hands.__new__(Hands)
        hands._settings = settings or MagicMock(capability_install_enabled=False)
        hands._session_state = None
        return {s["function"]["name"] for s in hands._tool_schemas()}

    return _names


class TestNothingIsOfferedWithoutAnExecutor:
    def test_browser_verbs_are_hidden_while_the_bridge_is_absent(
        self, schema_names
    ) -> None:
        from src.agent.browser_bridge import _get_bridge

        assert _get_bridge() is None, "fixture assumption changed"
        assert not (_BROWSER_TOOLS & schema_names())

    def test_the_two_with_no_reader_are_always_hidden(self, schema_names) -> None:
        """Unlike the others these never come back on a probe: there is
        no reader to detect, in either engine."""
        assert not (_TOOLS_WITH_NO_READER & schema_names())

    def test_verbs_that_make_unusable_tools_are_hidden(self, schema_names) -> None:
        """create_tool makes something the worker never offers and
        execute_direct cannot run. Offering it teaches the model it built
        a tool; the person then asks for it and nothing answers."""
        names = schema_names()
        assert not (_DYNAMIC_TOOL_VERBS & names)
        assert "list_tools" in names  # it works, and it tells the truth

    def test_a_created_tool_really_is_unreachable(self) -> None:
        """The reason for hiding, pinned — so the day someone wires dynamic
        tools back in, this fails and says to unhide the verbs.

        Two halves: the worker would not offer it, and the executor would
        not run it."""
        import asyncio

        from src.agent.tools.direct import execute_direct
        from src.agent.tools.registry import (
            Tool,
            register_dynamic_tool,
            unregister_dynamic_tool,
        )

        register_dynamic_tool(Tool(name="weather_now", description="погода"))
        try:
            hands = Hands.__new__(Hands)
            hands._settings = MagicMock(capability_install_enabled=False)
            hands._session_state = None
            offered = {s["function"]["name"] for s in hands._tool_schemas()}
            assert "weather_now" not in offered
            result = asyncio.run(execute_direct("weather_now", "{}"))
            assert result["success"] is False
            assert "Unknown direct tool" in result["error"]
        finally:
            unregister_dynamic_tool("weather_now")

    def test_the_worker_still_has_real_work(self, schema_names) -> None:
        """A gate that hides everything would pass all of the above."""
        names = schema_names()
        assert {"bash", "read", "write", "remember", "recall"} <= names
        assert len(names) > 25


class TestTheGateIsProbedNotHardcoded:
    def test_browser_verbs_return_when_an_extension_is_paired(
        self, monkeypatch
    ) -> None:
        import src.agent.browser_bridge as bb

        monkeypatch.setattr(
            bb, "_get_bridge", lambda: SimpleNamespace(connected=True)
        )
        assert not (_BROWSER_TOOLS & unreachable_tools())

    def test_a_bridge_with_nothing_paired_to_it_is_still_no_browser(
        self, monkeypatch
    ) -> None:
        """The daemon binds the bridge at boot whether or not Chrome ever
        shows up. A bound socket is not a browser: every call through it
        comes back "Browser not connected", and a verb that always fails
        is worse than one that is not offered."""
        import src.agent.browser_bridge as bb

        monkeypatch.setattr(
            bb, "_get_bridge", lambda: SimpleNamespace(connected=False)
        )
        assert _BROWSER_TOOLS <= unreachable_tools()

    def test_a_probe_that_explodes_hides_rather_than_crashes(
        self, monkeypatch
    ) -> None:
        """Accounting for reachability must never break a turn. If the
        probe cannot answer, assume unreachable — the safe direction."""
        import src.agent.browser_bridge as bb

        def boom():
            raise RuntimeError("no bridge module today")

        monkeypatch.setattr(bb, "_get_bridge", boom)
        assert _BROWSER_TOOLS <= unreachable_tools()


class TestEveryHiddenNameIsReal:
    def test_no_gate_entry_names_a_tool_that_does_not_exist(self) -> None:
        """A typo here silently gates nothing; the name would just never
        match. Pin the spelling against the tool table."""
        from src.agent.tools.system import TOOLS

        defined = {t.name for t in TOOLS}
        declared = (
            _BROWSER_TOOLS | _TOOLS_WITH_NO_READER | _DYNAMIC_TOOL_VERBS
        )
        assert declared <= defined, f"gate names no such tool: {declared - defined}"
