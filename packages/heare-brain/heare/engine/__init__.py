"""The engine: what there is to say, when it may be said, how it is said.

A part, like the others: the core does not know it exists. It reads the
stream of events and the snapshot of state, keeps intents, and puts
messages into the same quiet lane results already arrive on. The design is
in ``docs/engine.md``.

For now only the intent — step D1.
"""

from __future__ import annotations

from heare.engine.engine import Engine
from heare.engine.intent import Intent
from heare.engine.store import Intents

__all__ = ["Engine", "Intent", "Intents"]
