"""Re-export shim — the real module lives at :mod:`src.text.hallucinations`.

``is_junk`` moved out of ``spine`` because ``src/memory/extractor.py``
needed it too, and a lower layer (memory) importing up into the engine
that uses it (spine) was a soft import cycle: memory -> spine -> memory.
The filter itself has no dependencies, so it now lives in the
dependency-free ``src.text`` package that both ``spine`` and ``memory``
can import down into.

This shim exists only because ``src/spine/main.py`` still does
``from src.spine.hallucinations import is_junk`` and that file is off
limits here (owned by another change in flight). Once that import is
updated to point at ``src.text.hallucinations`` directly, this file can
be deleted.
"""
from __future__ import annotations

from src.text.hallucinations import is_junk

__all__ = ["is_junk"]
