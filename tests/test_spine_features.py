"""Switching a subsystem off must mean it is not there.

The point of these is a bad evening: something misbehaves, the cause is
not obvious, and the fastest answer is to remove suspects one at a time.
That only works if `off` means unwired — a subsystem that is still built
but told to keep quiet can still be the thing that is wrong.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.spine.features import FEATURES, describe, losses, resolve


def test_the_defaults_are_the_ones_declared() -> None:
    """Not "everything on" — `watcher` is deliberately off, because a
    thing that watches should be switched on by a person rather than
    inherited from a default. What must hold is that resolving with no
    configuration changes nothing."""
    state = resolve(SimpleNamespace())
    assert state == {f.name: f.default for f in FEATURES}
    assert state["watcher"] is False


def test_config_table_switches_one_off() -> None:
    settings = SimpleNamespace(spine_features={"roles": False})
    state = resolve(settings)
    assert state["roles"] is False
    assert state["wake"] is True


def test_cli_without_beats_the_config() -> None:
    settings = SimpleNamespace(spine_features={"mcp": True})
    state = resolve(settings, without="mcp, roles")
    assert state["mcp"] is False and state["roles"] is False


def test_env_beats_the_cli() -> None:
    """A boot that is already going badly is driven from the shell."""
    import os

    os.environ["HEARE_WITHOUT"] = "tools"
    try:
        state = resolve(SimpleNamespace(spine_features={"tools": True}))
        assert state["tools"] is False
    finally:
        del os.environ["HEARE_WITHOUT"]


def test_safe_mode_switches_everything_optional_off() -> None:
    import os

    os.environ["HEARE_SAFE_MODE"] = "1"
    try:
        state = resolve(SimpleNamespace())
        assert not any(state.values())
    finally:
        del os.environ["HEARE_SAFE_MODE"]


def test_unknown_name_is_reported_not_obeyed(caplog) -> None:
    state = resolve(SimpleNamespace(), without="teleport")
    assert state == {f.name: f.default for f in FEATURES}, (
        "a typo must not silently disable something"
    )
    assert "no such feature" in caplog.text


def test_the_log_line_names_what_is_off() -> None:
    state = resolve(SimpleNamespace(), without="roles")
    line = describe(state)
    assert "roles=OFF" in line and "wake=on" in line


def test_losses_are_in_the_user_s_language() -> None:
    """A feature silently missing is worse than a bug: the log must say
    what stopped working, not just which flag flipped."""
    text = " ".join(losses(resolve(SimpleNamespace(), without="roles,memory")))
    assert "мітинг" in text and "пам" in text
    # `watcher` and `hear_all`, the two that are off until asked for.
    # Their lines still have to say what is missing, because "off by
    # default" is not "not a loss".
    assert [line.split(":")[0] for line in losses(resolve(SimpleNamespace()))] == [
        "watcher",
        "hear_all",
    ]


def _feature_lookups_in_source() -> dict[str, list[str]]:
    """Every feature name the code under src/ actually asks about.

    The scan is deliberately literal — it looks for the three shapes a
    lookup can have, with the name spelled out:

        features["mcp"]      features.get("mcp")      _feature(loop, "mcp")

    A name that appears nowhere in that form is a switch nothing reads:
    the table would still print it, the dashboard would still show it,
    and turning it off would change nothing. Anything cleverer (an
    import graph, a runtime trace) would have to boot the engine; this
    is a grep, and a grep is what a lying switch survives today.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src"
    pattern = re.compile(
        r"""features\s*\[\s*["']([a-z_]+)["']\s*\]"""
        r"""|features\s*\.\s*get\(\s*["']([a-z_]+)["']"""
        r"""|_feature\([^)]*?["']([a-z_]+)["']\s*\)"""
    )
    found: dict[str, list[str]] = {}
    for path in root.rglob("*.py"):
        if "node_modules" in path.parts:
            continue
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in pattern.finditer(text):
            name = next(g for g in match.groups() if g)
            found.setdefault(name, []).append(str(path))
    return found


def test_every_declared_feature_is_actually_consulted() -> None:
    """A switch that nothing reads is worse than no switch at all.

    `mcp` and `telemetry` were in this table for months while the daemon
    started the MCP servers and the telemetry writer unconditionally: the
    log said off, the dashboard said off, and `npx` still spawned. This
    test is the tripwire — add a name to FEATURES and forget to wire it,
    and the suite says so immediately.
    """
    consulted = _feature_lookups_in_source()
    unwired = sorted(f.name for f in FEATURES if f.name not in consulted)
    assert not unwired, (
        "declared in FEATURES but consulted nowhere in src/: "
        + ", ".join(unwired)
        + " — switching it off would change nothing, so the log and the "
        "dashboard would be lying. Gate the subsystem on "
        'features["<name>"] where it is built, or drop the name.'
    )


def test_the_consistency_scan_can_see_the_names_it_claims_to_see() -> None:
    """The tripwire above is a regex; a regex that matches nothing would
    pass silently forever. Pin what it found, and where."""
    consulted = _feature_lookups_in_source()
    # The composition root switches these seven.
    for name in ("usage", "aec", "memory", "roles", "tools", "wake", "persist"):
        assert any(
            p.endswith("src/spine/main.py") for p in consulted[name]
        ), f"{name} is no longer switched in the composition root"
    # These two are built by the daemon's runner, so that is where their
    # switch has to bite.
    for name in ("mcp", "telemetry"):
        assert any(
            p.endswith("src/daemon/spine_engine.py") for p in consulted[name]
        ), f"{name} is no longer switched in the spine engine"


@pytest.mark.parametrize("name", ["aec", "wake", "tools", "roles", "mcp"])
async def test_the_engine_builds_without_each_one(name, tmp_path) -> None:
    """Not a smoke test: the composition root must skip the wiring, and
    the conductor must still be a working conductor without it."""
    import src.spine.main as spine_main

    settings = SimpleNamespace(
        db_path=tmp_path / "h.db",
        workspace_dir=tmp_path / "ws",
        groq_api_key="x",
        groq_language="uk",
        deepseek_api_key="sk-test",
        deepseek_base_url="",
        deepseek_model="",
        identity_file=tmp_path / "identity.json",
        wake_word="гава",
        wake_window_seconds=45.0,
        wake_required=True,
        spine_vad_stop_ms=800,
        spine_turn_continuation_hold_seconds=2.6,
    )
    loop = await spine_main._build_loop(
        settings, audio=None, voice="", hold_s=1.3, full=True, without=name
    )
    try:
        attr = {
            "aec": "aec", "wake": "wake", "tools": "toolbox",
            "roles": "role_flow", "mcp": "mcp",
        }[name]
        assert getattr(loop, attr, None) is None, f"{name} was wired anyway"
        # Whatever was removed, the engine is still an engine.
        assert loop.transcribe is not None and loop.synthesise is not None
    finally:
        await spine_main._close_loop(loop)
