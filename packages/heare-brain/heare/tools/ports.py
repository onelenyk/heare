"""What the tools need from the world without naming the part that owns it.

The toolbox speaks the model's language; the reminder store is the engine's.
These protocols are the seam: the catalog below imports them, never the
engine.
"""

from __future__ import annotations

from typing import Protocol


class Reminder(Protocol):
    id: int
    what: str
    not_before: float | None


class Reminders(Protocol):
    def hold(self, what: str, why: str, weight: int,
             about: str = "", until: float | None = None,
             not_before: float | None = None) -> object: ...

    def reminders(self, now: float) -> list[Reminder]: ...

    def cancel(self, intent_id: int, *, why: str = "reminder") -> bool: ...


class Devices(Protocol):
    def acting(self) -> dict[str, dict]:
        """By pid: the part's channel, the acts it offers, its last values."""
        ...

    def act(self, pid: str, name: str, value: object) -> bool: ...
