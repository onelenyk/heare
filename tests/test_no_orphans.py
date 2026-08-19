"""Nothing in src/ may be reachable only from tests.

This is the tripwire for the failure this repository actually had, which
was not dead code — dead code is cheap — but dead code with a green test
around it.

When the previous engine was deleted, what died was not its files. It was
everything only it had called: a notification facade, a context builder, a
conversation manager, three copies of a voice-for-language lookup. Each of
those looked alive from the inside. Each had tests. And every one of those
tests passed, because a test builds its own subject — `NotificationBackend()`
works perfectly in a test file, and cannot possibly know that no other line
in the program ever writes those parentheses.

So the suite kept reporting that a subsystem worked, for months, while it
was not in the running process at all. That is worse than silence: it is
the instrument reading full when the tank is empty.

The check is a grep, deliberately. Anything cleverer — an import graph, a
runtime trace — would have to boot the daemon, and the thing being caught
here is exactly what survives not being booted. A grep has false positives
(dynamic dispatch, string-keyed registries, subclass overrides), which is
what ALLOWED is for: every entry names a reason, so the next person can
tell "deliberately kept" from "quietly rotting".
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"


# Names that are genuinely referenced only by tests, with the reason.
# Adding to this list is a decision; the point of the test is that it has
# to be made out loud.
ALLOWED: dict[str, str] = {
    # Deliberate: the un-register half of a live pair. src/menubar.py calls
    # set_host_hooks and never releases them; wiring the teardown is a fix,
    # not a deletion.
    "clear_host_hooks": "teardown half of a live pair — needs wiring, 2026-08-19",
    # Deliberate: replays a role session from the DB so it survives a
    # restart mid-session. src/spine/role_flow.py uses the in-memory log
    # instead; connecting this is a feature, not a cleanup.
    "role_session_turns": "restart-resilience for role sessions — unfinished, 2026-08-19",
    # Deliberate: the eager path for the capability index. The lazy
    # fallback (_get_or_build_capability_index) covers every caller, and
    # the tests use this to inject a fake.
    "set_capability_index": "test seam for the capability index, 2026-08-19",
}


def _public_definitions() -> dict[str, Path]:
    """Every public top-level or class-level name defined under src/."""
    found: dict[str, Path] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "frontend" in path.parts or "node_modules" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            if node.name.startswith("_") or node.name.startswith("test_"):
                continue
            found.setdefault(node.name, path)
    return found


def _read(root: Path, skip_frontend: bool = True) -> str:
    parts = []
    for path in root.rglob("*.py"):
        if skip_frontend and ("frontend" in path.parts or "node_modules" in path.parts):
            continue
        try:
            parts.append(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(parts)


def test_nothing_in_src_is_reachable_only_from_tests() -> None:
    src_text = _read(SRC)
    test_text = _read(TESTS, skip_frontend=False)

    orphans: dict[str, Path] = {}
    for name, path in _public_definitions().items():
        if name in ALLOWED:
            continue
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        # One hit in src/ is the definition itself.
        if len(word.findall(src_text)) > 1:
            continue
        if word.search(test_text) is None:
            continue
        orphans[name] = path.relative_to(ROOT)

    assert not orphans, (
        "these exist, are tested, and nothing in the running system reaches "
        "them — wire them up, delete them, or add them to ALLOWED with a "
        "reason:\n"
        + "\n".join(f"  {n:34} {p}" for n, p in sorted(orphans.items()))
    )


def test_every_allowance_is_still_needed() -> None:
    """An allowance that has outlived its reason is the same lie in
    smaller print: it says "known and accepted" about something that may
    since have been deleted or connected."""
    defined = _public_definitions()
    src_text = _read(SRC)

    stale = []
    for name, reason in ALLOWED.items():
        if name not in defined:
            stale.append(f"{name}: gone from src/ — drop the allowance ({reason})")
            continue
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        if len(word.findall(src_text)) > 1:
            stale.append(f"{name}: reached from src/ now — drop the allowance")

    assert not stale, "\n".join(stale)
