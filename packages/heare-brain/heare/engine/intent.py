"""One thing the assistant wants to tell the person.

Not a phrase — the model writes the phrase when the moment comes. An intent
is what there is to say, why, how much it matters, and until when it is
worth saying at all. This is only the blank form: it does nothing and goes
nowhere.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Intent:
    id: int
    what: str                    # the point, in one sentence
    why: str                     # the event behind it: `task_done`, `reminder`…
    weight: int                  # 1 a trifle · 2 worth knowing · 3 needs your action
    about: str = ""              # the task or topic; duplicates merge on this
    # `until` is when it stops mattering; `not_before` is when it starts.
    until: float | None = None   # past this it means nothing and goes away
    not_before: float | None = None  # before this it is not worth saying yet
    status: str = "held"         # held · delivered · stale · dropped
    made: float = field(default_factory=time.time)
    delivered_at: float | None = None  # when it was told, for the daily budget

    def alive(self, now: float) -> bool:
        """Still worth saying: not yet told, due, and not too late."""
        return (
            self.status == "held"
            and (self.not_before is None or now >= self.not_before)
            and (self.until is None or now < self.until)
        )
