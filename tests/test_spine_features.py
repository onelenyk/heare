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


def test_everything_is_on_by_default() -> None:
    state = resolve(SimpleNamespace())
    assert all(state.values())
    assert set(state) == {f.name for f in FEATURES}


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
    assert all(state.values()), "a typo must not silently disable something"
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
    assert losses(resolve(SimpleNamespace())) == []


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
