"""Attention's shape, kept by a test rather than by memory.

A feature that grows by adding modules spreads through the app. Attention
grows by one row in its own table, and this file holds the edge of that
room: nothing outside ``engine/`` may import it, and its public methods are
exactly the ones the engine already uses — a new one is a decision, not a
drift. The same idea as ``test_dependencies.py``, one room smaller.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from heare.engine.soma import Soma

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"
ENGINE = PACKAGES / "heare-brain" / "heare" / "engine"
SOMA = ENGINE / "soma.py"

# Room to finish a thought, not to grow a wing. Raised deliberately, with a
# reason in the commit, the way CORE_LINES is. 10 November 2026: +20 for
# forgetting (a said or carried want fades) and the mood that follows it;
# +20 more for the bounded list — a dozen wants, ranked by pressing and
# age, and a drop that costs no energy; +20 for the one trim that holds
# between ticks, and the last_spoken clear that keeps the invariants.
SOMA_LINES = 480

# The engine's whole conversation with attention. ``feed`` and ``tick`` are
# what happens; ``spoke``, ``engage``, ``ignored`` are how the person
# answered; ``state`` is the one sentence the model reads; ``save`` and
# ``load`` are its own file. Nothing else belongs here without a decision.
PUBLIC = {
    "feed", "tick", "hottest", "spoke", "engage", "ignored",
    "state", "thing", "save", "load", "to_dict", "from_dict",
}


def _soma_names(path: Path) -> set[str]:
    """What a file pulls out of attention, under any spelling of the import."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "heare.engine.soma" or alias.name.startswith("heare.engine.soma."):
                    found.add("*")
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            if module == "heare.engine.soma":
                found.update(alias.name for alias in node.names)
            elif module == "heare.engine":
                found.update(alias.name for alias in node.names if alias.name == "Soma")
    return found


def test_only_the_engine_imports_the_attention():
    guilty = [
        str(path.relative_to(PACKAGES))
        for path in PACKAGES.rglob("*.py")
        if "__pycache__" not in path.parts
        and ENGINE not in path.parents
        and _soma_names(path)
    ]
    assert not guilty, f"attention leaks outside engine/: {guilty}"


def test_attention_offers_nothing_but_what_the_engine_uses():
    offered = {
        name
        for name, value in vars(Soma).items()
        if not name.startswith("_")
        and (inspect.isfunction(value) or isinstance(value, (classmethod, staticmethod)))
    }
    assert offered == PUBLIC, f"attention's public methods changed: {offered ^ PUBLIC}"


def test_attention_stays_small():
    lines = len(SOMA.read_text(encoding="utf-8").splitlines())
    assert lines <= SOMA_LINES, f"attention is {lines} lines"
