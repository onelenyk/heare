"""The laws: one source — the prompt text and the gate for actions.

They cannot drift apart: both the prompt and the check read from here. The
reasoning is in ``docs/laws.md``; this file is *what*.

The laws are a part of the brain, not the whole of it: the text guides the
model, but the model is not the enforcement. What is enforced lives in
``RISKY`` and in the gate, in code.
"""

from __future__ import annotations

# Carried in every prompt. It guides how the model phrases and grades the
# grey zone — it is not the safety; RISKY and the gate are.
TEXT = (
    "0. The owner above all. I am their instrument; their interest and household "
    "are my only aim. Outside it is not my concern.\n"
    "1. Before a risky act I find the concrete consequence, name it, ask once, "
    "then act. A yes after the named consequence lifts the pause — for anything.\n"
    "2. My own aims rank below the owner's will: I propose, I do not impose.\n"
    "3. No change passes if it breaks or degrades.\n"
)

# Bright lines: these kinds of act always pass the gate — a pause and a named
# consequence. The decision is code, never the model's mood.
RISKY = ("money", "delete", "overwrite", "outward", "process", "secret", "self_edit")


def risky(kind: str) -> bool:
    """Whether this kind of act must be paused, and its consequence named."""
    return kind in RISKY
