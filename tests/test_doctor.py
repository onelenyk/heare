"""`make doctor` has to run, and it has to not lie.

Running is most of it: this is the instrument reached for when something
is already wrong, and an instrument that raises on the bad day is worse
than none. So it builds the whole engine with no device and reports
whatever it finds, including that the engine did not build.

The second test is the one with teeth. Three subsystems read OFF in this
process for a reason that has nothing to do with whether they work — `aec`
needs a microphone the doctor does not open, `mcp` and `telemetry` are
built by the daemon runner. Printing "off" for those would be the exact
fault this instrument exists to catch, committed by the instrument.
"""

from __future__ import annotations

import asyncio

import pytest

from src.doctor import _NOT_OURS_TO_SAY, _plugin_section, _survey, _tool_section


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """Settings that belong to no machine: a throwaway ~/.heare and a fake
    key. The doctor builds a whole engine, and an engine built from this
    laptop's keys and database is a test that passes here and nowhere
    else — CI included."""
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "HEARE_HOME", tmp_path)
    for var in ("DEEPSEEK_API_KEY", "ZAI_API_KEY", "OPENCODE_API_KEY",
                "OPENROUTER_API_KEY", "HEARE_WITHOUT", "HEARE_SAFE_MODE"):
        monkeypatch.delenv(var, raising=False)
    settings = cfg_mod.load_settings()
    settings.deepseek_api_key = "sk-test"
    return settings


def test_an_empty_register_says_so_rather_than_looking_broken(monkeypatch) -> None:
    import src.plugins as plugins_mod

    monkeypatch.setattr(plugins_mod, "_REGISTERED", {})
    lines = _plugin_section()
    assert lines and any("порожньо" in line for line in lines)


def test_a_moved_feature_is_answered_by_its_plugin_not_twice(isolated_settings) -> None:
    """`usage` moved into the register. It must appear there, in its own
    words, and not also in the old-style section below it — a feature
    listed twice with two different answers is worse than either."""
    plugin_lines, feature_lines = asyncio.run(_survey(isolated_settings))
    assert any(line.strip().startswith("usage") for line in plugin_lines)
    assert not any(line.strip().startswith("usage") for line in feature_lines)


def test_the_tool_section_counts_what_the_worker_would_offer() -> None:
    from src.agent.hands import unreachable_tools
    from src.agent.tools.system import TOOLS

    lines = _tool_section()
    declared = len({t.name for t in TOOLS if t.enabled})
    assert f"оголошено      {declared}" in "\n".join(lines)
    # Whatever is hidden is named, not just counted: a number alone sends
    # someone back to grep, which is the day being replaced here.
    for name in unreachable_tools():
        assert name in "\n".join(lines)


def test_what_the_doctor_cannot_judge_is_labelled_not_guessed(isolated_settings) -> None:
    """`?` with a reason, never `OFF`."""
    _, lines = asyncio.run(_survey(isolated_settings))
    for name, why in _NOT_OURS_TO_SAY.items():
        row = next(line for line in lines if line.strip().startswith(name))
        assert "OFF" not in row, (
            f"{name} is reported off, and this process cannot know that: {why}"
        )


def test_the_unjudgeable_are_really_unjudgeable() -> None:
    """A name in that list that the doctor *could* answer would be an
    excuse rather than a caveat. These three are structural: no audio
    device is opened here, and this is not the daemon runner."""
    assert set(_NOT_OURS_TO_SAY) == {"aec", "mcp", "telemetry"}
