"""The boundary between the core and everything else.

Measured 9 September 2026, by walking the AST: twelve modules hold the
whole conversation — ear, voice activity, the turn clock, speech to text,
the model, text to speech, the conductor over them, the prompt it is
given and the watchdog on its own hearing — and **not one of them imports
anything else from `src/spine/`**. 2 946 lines, zero internal edges.

That did not happen by design. It happened because of one rule, written
in the header of `loop.py` and followed since:

    imports no sibling module: every collaborator is injected

Applied consistently, that rule *is* a plugin architecture. What it has
never had is a check. The boundary lives entirely on discipline, and one
careless import — `from src.spine.roles import ...` at the top of
`loop.py`, added in a hurry to fix something real — would erase it
silently. Nothing would fail. Nobody would notice until the day someone
tried to run the core without roles and found they could not.

So this is the ratchet. It is the first thing built in the core/plugins
migration (docs/core-and-plugins.md) because every later phase leans on
the boundary holding, and a boundary nothing measures is a wish.

What it does NOT claim: that the twelve are the *right* twelve. That is a
design decision recorded in the plan and revisable there — see the
migration's Phase 4, which is allowed to conclude that the engine belongs
inside. It claims only that the set named below is closed under imports,
which is what makes moving anything else safe.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SPINE = pathlib.Path(__file__).resolve().parents[1] / "src" / "spine"

# The conversation, and nothing else. A module belongs here when deleting
# every feature would leave it doing its job: hear a sentence, decide,
# answer, say it, and notice when the ear stops working.
CORE: frozenset[str] = frozenset(
    {
        "loop",  # the conductor
        "audio_io",  # ear and mouth
        "vad",  # is anyone speaking
        "turn",  # when a turn has ended
        "stt",  # words out of sound
        "llm",  # the answer
        "tts",  # sound out of words
        "sentences",  # where to break the reply for the speaker
        "voicing",  # which voice the script calls for
        "hallucinations",  # what Whisper invents in silence
        "prompt",  # what the model is told it is
        "hearing",  # whether it can still hear at all
    }
)


def _spine_imports(path: pathlib.Path) -> set[str]:
    """Every `src.spine.<name>` this module pulls in, at any depth.

    Deliberately literal, and deliberately includes imports made inside
    functions: a deferred import is still an edge, and `loop.py` is
    allowed none of either.
    """
    found: set[str] = set()
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("src.spine"):
                tail = module.split(".")
                if len(tail) > 2:
                    found.add(tail[-1])
                else:
                    # `from src.spine import tools` — the names are the modules.
                    found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src.spine."):
                    found.add(alias.name.split(".")[-1])
    return found


def _core_files() -> list[pathlib.Path]:
    return [SPINE / f"{name}.py" for name in sorted(CORE)]


# ── the boundary ──────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _core_files(), ids=lambda p: p.stem)
def test_a_core_module_imports_no_feature(path: pathlib.Path) -> None:
    """The whole ratchet, in one assertion per module.

    If this fails, read the failure as a question rather than a mistake:
    either the import is wrong, or the thing being imported belongs in the
    core and CORE above should say so — on purpose, in a commit that says
    why.
    """
    outside = _spine_imports(path) - CORE
    assert not outside, (
        f"{path.name} imports {sorted(outside)} from src/spine/. "
        "The core must not depend on a feature — inject it instead, the "
        "way loop.py takes every collaborator as a field."
    )


def test_the_core_is_closed_under_imports() -> None:
    """Not just feature-free — closed. Everything a core module needs is
    itself core, so the set can be lifted out whole."""
    reachable: set[str] = set()
    for path in _core_files():
        reachable |= _spine_imports(path)
    assert reachable <= CORE


def test_every_named_core_module_exists() -> None:
    """A typo here would gate nothing: the name would simply never match a
    file, and the module it meant to protect would go unchecked."""
    missing = [name for name in CORE if not (SPINE / f"{name}.py").exists()]
    assert not missing, f"CORE names files that are gone: {missing}"


def test_the_conductor_imports_no_sibling_at_all() -> None:
    """`loop.py` is stricter than the rest and says so in its own header.
    It owns order and lifecycle; the modules own their I/O. It is what
    lets a whole conversation be played out on fakes before a single audio
    device or socket exists — which is why the suite runs in 82 seconds."""
    assert _spine_imports(SPINE / "loop.py") == set()


# ── the boundary is worth something ───────────────────────────────────


def test_the_core_is_small() -> None:
    """A core that quietly grows is a core that stops being one. The
    number is not sacred; a commit that raises it should be a commit that
    argues for it."""
    lines = sum(len(p.read_text().splitlines()) for p in _core_files())
    assert lines < 3_500, f"the core is {lines} lines — it was 2 946 on 9 Sep 2026"


def test_the_features_outnumber_the_core() -> None:
    """A sanity check on the claim the migration rests on: most of this
    project is optional. If this ever fails, either a great deal was
    deleted or the core swallowed something large."""
    core = sum(len(p.read_text().splitlines()) for p in _core_files())
    rest = sum(
        len(p.read_text().splitlines())
        for p in SPINE.glob("*.py")
        if p.stem not in CORE
    )
    assert rest > core
