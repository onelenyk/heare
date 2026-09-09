"""The worker must not offer what nothing can carry out.

63 tools are defined; 62 are enabled; and for a month the daemon handed
the model schemas for 17 of them whose other half does not exist in this
process. ``set_agent_manager`` is called from nowhere since
``src/pipeline/build.py`` was deleted on 17 August, so
``get_agent_manager()`` is permanently None and all nine agent verbs
503. ``BrowserBridge`` is constructed nowhere at all under the spine —
it starts after an early return in ``src/main.py`` — so all eight
browser verbs find no bridge.

Two more are worse than broken: ``sidetone`` and ``vad_sensitivity``
execute cleanly, write State, and return success to a reader that does
not exist in either engine.

A tool the model can see is a promise to the person on the microphone.
"""
from __future__ import annotations

from types import SimpleNamespace

from unittest.mock import MagicMock

import pytest

from src.agent.hands import (
    _AGENT_TOOLS,
    _BROWSER_TOOLS,
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
    def test_agent_verbs_are_hidden_while_the_manager_is_absent(
        self, schema_names
    ) -> None:
        from src.agent.subagent_manager import get_agent_manager

        assert get_agent_manager() is None, "fixture assumption changed"
        assert not (_AGENT_TOOLS & schema_names())

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

    def test_the_worker_still_has_real_work(self, schema_names) -> None:
        """A gate that hides everything would pass all of the above."""
        names = schema_names()
        assert {"bash", "read", "write", "remember", "recall"} <= names
        assert len(names) > 25


class TestTheGateIsProbedNotHardcoded:
    def test_agent_verbs_return_when_a_manager_exists(self, monkeypatch) -> None:
        """The list must not outlive the outage it describes: wire the
        manager back and the tools reappear without editing this file."""
        import src.agent.subagent_manager as sm

        monkeypatch.setattr(sm, "get_agent_manager", lambda: object())
        assert not (_AGENT_TOOLS & unreachable_tools())

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
        import src.agent.subagent_manager as sm

        def boom():
            raise RuntimeError("no manager module today")

        monkeypatch.setattr(sm, "get_agent_manager", boom)
        assert _AGENT_TOOLS <= unreachable_tools()


class TestEveryHiddenNameIsReal:
    def test_no_gate_entry_names_a_tool_that_does_not_exist(self) -> None:
        """A typo here silently gates nothing; the name would just never
        match. Pin the spelling against the tool table."""
        from src.agent.tools.system import TOOLS

        defined = {t.name for t in TOOLS}
        declared = _AGENT_TOOLS | _BROWSER_TOOLS | _TOOLS_WITH_NO_READER
        assert declared <= defined, f"gate names no such tool: {declared - defined}"
