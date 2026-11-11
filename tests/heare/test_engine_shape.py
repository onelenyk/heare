"""The engine's room, kept like the core's and attention's.

Policy (may it be said), timing (the gap, the moments), delivery framing,
and attention's adapter share one class. The budget is not a style rule: it
says when the class stops being a room. When the clock gains a planner the
split is due — not before.
"""

from pathlib import Path

ENGINE = (
    Path(__file__).resolve().parents[2]
    / "packages" / "heare-brain" / "heare" / "engine" / "engine.py"
)
# 580: 11 November 2026, +35 for listing attention's entries to the model
# and merging two that are the same unfinished thing. The merge is attention's;
# the engine only drops the lost note.
ENGINE_LINES = 580


def test_the_engine_stays_in_its_room():
    lines = len(ENGINE.read_text(encoding="utf-8").splitlines())
    assert lines <= ENGINE_LINES, f"engine.py is {lines} lines"
