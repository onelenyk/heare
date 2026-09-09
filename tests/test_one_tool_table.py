"""There is one hand-maintained tool table, and it is system.py's.

Two of them drifted for three months. ``src/agent/tools/system.py`` is
what the worker turns into schemas, so it is what the model is offered;
``src/agent/tools/registry.py`` held a parallel dict that fed the
capability index. The May refactor that introduced system.py said in its
own plan «update registry.py, schemas.py, direct.py — keep backward
compat or delete», kept backward compat, and nobody came back.

Measured 9 September 2026, before the merge: 64 tools in one table, 53 in
the other. Twelve names the model can be offered — delegate,
look_at_screen, read_display, clear_display, mute_mic, mute_bot, volume,
mic_gain, sidetone, vad_sensitivity, audio_input, audio_output — did not
exist in the index at all, so a spoken intent could never find them.
Forty-one of the 53 shared names carried a different description.

registry.TOOLS is projected from system.TOOLS now, so the two cannot
disagree. This is the ratchet against a second table appearing again.
"""

from __future__ import annotations

import ast
import pathlib

from src.agent.tools.registry import TOOLS as INDEX_TOOLS
from src.agent.tools.system import TOOLS as WORKER_TOOLS

REGISTRY = pathlib.Path(__file__).resolve().parents[1] / "src/agent/tools/registry.py"


def test_the_index_and_the_worker_know_the_same_tools() -> None:
    """The property that was false for three months."""
    assert set(INDEX_TOOLS) == {t.name for t in WORKER_TOOLS}


def test_the_descriptions_are_the_same_text() -> None:
    """Not just the same names — the same words.

    The index is searched by a spoken intent, and it should be searching
    the sentence the model will actually read, not an older draft of it.
    """
    worker = {t.name: t.description for t in WORKER_TOOLS}
    assert {n: t.description for n, t in INDEX_TOOLS.items()} == worker


def test_registry_declares_no_tools_of_its_own() -> None:
    """The AST check, so this cannot be satisfied by a second table that
    happens to agree today.

    A literal ``Tool(...)`` at module level in registry.py is a tool
    declared in the wrong file — the declaration belongs in system.py,
    beside its handler and its schema.
    """
    tree = ast.parse(REGISTRY.read_text(), filename=str(REGISTRY))
    declared = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Tool"
        and any(
            isinstance(arg, ast.Constant) for arg in [*node.args, *(k.value for k in node.keywords)]
        )
    ]
    assert not declared, (
        f"{len(declared)} tools declared literally in registry.py. "
        "Tools are declared once, in system.py, next to their handler."
    )
