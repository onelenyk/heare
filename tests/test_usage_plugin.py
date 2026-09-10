"""usage, the first feature to answer for itself.

Three states the old wiring could not tell apart, and one hole it could
not see. The hole: switching `usage` off stopped every writer but one —
spine/vision.py found no ledger handed to it and opened a SQLite
connection of its own on every look. "Off means not wired at all" is the
promise in src/spine/features.py, and that path had never been told.
"""

from __future__ import annotations

import pathlib
import sqlite3
from types import SimpleNamespace

import pytest

from src.features import usage as usage_feature
from src.features.usage import UsagePlugin
from src.plugins import WireContext


def _rows(db: pathlib.Path) -> int:
    with sqlite3.connect(db) as con:
        return con.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]


@pytest.fixture
def plugin(monkeypatch):
    """The module's own PLUGIN is what writers reach through ledger_for(),
    so tests that exercise a writer have to wire *that* one — on a fresh
    instance swapped in, so no test inherits another's state."""
    fresh = UsagePlugin()
    monkeypatch.setattr(usage_feature, "PLUGIN", fresh)
    yield fresh
    if fresh._ledger is not None:
        fresh._ledger.close()


# ── the three states ──────────────────────────────────────────────────


def test_switched_on_it_is_working(plugin, tmp_path) -> None:
    ledger = plugin.wire(WireContext(SimpleNamespace(db_path=tmp_path / "h.db"), True))
    assert ledger is not None
    assert plugin.health().verdict == "ok"


def test_switched_off_it_builds_nothing_and_says_what_is_lost(plugin, tmp_path) -> None:
    assert plugin.wire(WireContext(SimpleNamespace(db_path=tmp_path / "h.db"), False)) is None
    health = plugin.health()
    assert health.verdict == "off"
    assert "витрати" in health.detail


def test_a_ledger_with_no_database_is_unreachable_not_fine(plugin, tmp_path) -> None:
    """The ledger swallows a failed connect and carries on writing into
    nothing; the dashboard then reads $0.00 in good faith. The column
    this plugin adds is the one place that failure becomes visible."""
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    plugin.wire(WireContext(SimpleNamespace(db_path=blocker / "h.db"), True))
    health = plugin.health()
    assert health.verdict == "UNREACHABLE"
    assert "h.db" in health.detail


def test_not_yet_wired_is_said_rather_than_guessed(plugin) -> None:
    health = plugin.health()
    assert health.verdict == "NOT WIRED"
    assert health.detail


# ── the hole ──────────────────────────────────────────────────────────


_LOOK = {"usage": {"prompt_tokens": 1200, "completion_tokens": 40, "cost": 0.00035}}


def test_a_look_is_not_counted_when_usage_is_off(plugin, tmp_path) -> None:
    """The regression this move fixes. Before it, this wrote a row."""
    from src.spine.vision import _record

    db = tmp_path / "h.db"
    settings = SimpleNamespace(db_path=db, vision_provider="openrouter")
    plugin.wire(WireContext(settings, False))

    _record(None, settings, "google/gemini-3.1-flash-lite", _LOOK)

    assert not db.exists() or _rows(db) == 0


def test_a_look_is_counted_in_the_one_ledger_when_usage_is_on(plugin, tmp_path) -> None:
    from src.spine.vision import _record

    db = tmp_path / "h.db"
    settings = SimpleNamespace(db_path=db, vision_provider="openrouter")
    plugin.wire(WireContext(settings, True))

    _record(None, settings, "google/gemini-3.1-flash-lite", _LOOK)

    assert _rows(db) == 1


def test_outside_an_engine_a_writer_still_gets_a_ledger(plugin, tmp_path) -> None:
    """A script or a tool run with no engine built has no switch to have
    flipped. It keeps what it had before the plugin: its own ledger."""
    ledger = usage_feature.ledger_for(SimpleNamespace(db_path=tmp_path / "h.db"))
    try:
        assert ledger is not None
    finally:
        ledger.close()


# ── the composition root and the dashboard ────────────────────────────


def _settings(tmp_path):
    return SimpleNamespace(
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


@pytest.mark.parametrize("without", ["", "usage"])
async def test_the_engine_is_wired_through_the_plugin(without, tmp_path) -> None:
    """What the conductor holds is what the plugin built — and the
    dashboard's answer comes from the plugin, with a verdict."""
    import src.spine.main as spine_main
    from src.daemon.spine_engine import wired_features
    from src.features.usage import PLUGIN

    loop = await spine_main._build_loop(
        _settings(tmp_path), audio=None, voice="", hold_s=1.3, full=True, without=without
    )
    try:
        assert loop.usage is PLUGIN._ledger
        entry = next(e for e in wired_features(loop) if e["name"] == "usage")
        if without:
            assert loop.usage is None
            assert (entry["on"], entry["verdict"]) == (False, "off")
        else:
            assert loop.usage is not None
            assert (entry["on"], entry["verdict"]) == (True, "ok")
    finally:
        await spine_main._close_loop(loop)


def test_the_plugin_is_in_the_one_list_of_installed_features() -> None:
    from src.features import installed

    assert "usage" in [p.name for p in installed()]
