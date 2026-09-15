"""The mouth — sentences in, sound out, in order, and stoppable.

One of the five parts in docs/archive/2026-09-16/design.html. The conductor decides *what*
is said; this decides nothing and owns the one thing speech needs that a
function call does not: a stream that is already playing while the next
part is still being made, and can be paused, resumed or stopped from
outside at any instant.

Imports nothing from the rest of the project. The speaker it plays into
is whatever was handed over — a device, or a queue in a test.
"""
from __future__ import annotations

from src.mouth.utterance import Mouth, Utterance

__all__ = ["Mouth", "Utterance"]
