"""The shape of the new app, enforced.

Three rules, and the reason for each is the old app: a core that 29
things were assigned onto from two files, parts that reached into each
other, and no line anyone could point at and say "that side knows
nothing about this side".

* ``heare.core`` imports the standard library and itself. Nothing else.
* A part imports the core, its own package, and third-party libraries —
  never another part.
* Nothing under ``heare/`` imports ``src``.

``heare.app`` and ``heare.listener`` are the two places allowed to know
several parts: one assembles the app, the other is the tool that listens
to it. ``heare.keys`` is a leaf any part may read a secret through.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "heare"
KNOW_EVERYTHING = {"app", "listener", "brain"}
LEAVES = {"heare.keys"}
# The core may grow, but not into the tangle it replaces. If this fails,
# something in the core is a part. Raised deliberately for the question
# channel (an executor asking the person mid-work), not by drift.
CORE_LINES = 1350


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module == "heare":
                # ``from heare import keys`` names a module, not the package.
                found.update(f"heare.{alias.name}" for alias in node.names)
            else:
                found.add(node.module)
    return found


def _part(path: Path) -> str:
    """The part a file belongs to: ``core``, ``channels.voice``, ``memory``…"""
    rel = path.relative_to(ROOT).with_suffix("").parts
    if rel[0] == "channels" and len(rel) > 2:
        return f"channels.{rel[1]}"
    return rel[0]


def _files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_nothing_new_imports_the_old_app():
    guilty = [
        str(p.relative_to(ROOT)) for p in _files()
        if any(m == "src" or m.startswith("src.") for m in _imports(p))
    ]
    assert not guilty, f"imports src: {guilty}"


def test_the_core_imports_only_itself_and_the_standard_library():
    guilty = []
    for path in (ROOT / "core").rglob("*.py"):
        for module in _imports(path):
            top = module.split(".")[0]
            if module.startswith("heare.core") or top in sys.stdlib_module_names:
                continue
            guilty.append(f"{path.name}: {module}")
    assert not guilty, f"the core reaches outside itself: {guilty}"


def test_a_part_imports_only_the_core_and_itself():
    guilty = []
    for path in _files():
        part = _part(path)
        if part in KNOW_EVERYTHING or part == "core" or path.parent == ROOT:
            continue
        for module in _imports(path):
            if not module.startswith("heare"):
                continue
            if module.startswith("heare.core") or module in LEAVES:
                continue
            if module.startswith(f"heare.{part}"):
                continue
            guilty.append(f"{path.relative_to(ROOT)}: {module}")
    assert not guilty, f"a part reaches into another part: {guilty}"


def test_the_core_stays_small():
    lines = sum(
        len(p.read_text(encoding="utf-8").splitlines())
        for p in (ROOT / "core").rglob("*.py")
    )
    assert lines <= CORE_LINES, f"the core is {lines} lines"
