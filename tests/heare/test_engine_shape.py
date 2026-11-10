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
# Room for one more thing, not for a fourth job in the same class.
ENGINE_LINES = 545


def test_the_engine_stays_in_its_room():
    lines = len(ENGINE.read_text(encoding="utf-8").splitlines())
    assert lines <= ENGINE_LINES, f"engine.py is {lines} lines"
