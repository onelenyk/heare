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

from src.doctor import _NOT_OURS_TO_SAY, _plugin_section, _tool_section


def test_the_empty_register_says_so_rather_than_looking_broken() -> None:
    lines = _plugin_section()
    assert lines and any("порожньо" in line for line in lines)


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


def test_what_the_doctor_cannot_judge_is_labelled_not_guessed() -> None:
    """`?` with a reason, never `OFF`."""
    from src.config import load_settings
    from src.doctor import _feature_section

    lines = asyncio.run(_feature_section(load_settings()))
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
