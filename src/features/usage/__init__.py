"""usage — the ledger of paid calls, as the first plugin.

First because it is the cheapest: a pure sink with no collaborators, so
the contract meets something simple before it meets roles or the engine.
If `wire()` and `health()` are awkward, this is where that shows, and it
is cheap to change here.

Moving it closed a hole the old wiring could not see. The composition
root honoured the `usage` switch — and `spine/vision.py`, finding no
ledger handed to it, opened a SQLite connection of its own on every look
and wrote to `usage_events` anyway. "Off means not wired at all" is the
promise in src/spine/features.py, and one path had never been told. Every
writer now asks `ledger_for()`, and the answer is the plugin's.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from src.features.usage.ledger import SpineUsage
from src.plugins import Health, PluginBase, WireContext

logger = logging.getLogger("heare.features.usage")


def _cost() -> str:
    """What switching this off costs, from the one table that says so."""
    from src.spine.features import FEATURES

    return next(f.cost for f in FEATURES if f.name == "usage")


class UsagePlugin(PluginBase):
    name = "usage"

    def __init__(self) -> None:
        self.cost = _cost()
        # Three states, and `health()` tells them apart:
        #   never wired in this process      _wired is False
        #   wired, switched off               _wired, not _enabled
        #   wired, switched on                _ledger is the one ledger
        self._wired = False
        self._enabled = False
        self._ledger: SpineUsage | None = None

    def wire(self, ctx: WireContext) -> SpineUsage | None:
        self._wired = True
        self._enabled = bool(ctx.enabled)
        self._ledger = SpineUsage(ctx.settings.db_path) if self._enabled else None
        return self._ledger

    def ledger_for(self, settings: Any) -> SpineUsage | None:
        """The ledger a writer should use, or None to count nothing.

        Three answers for the three states. Switched off is None — the
        whole point of the switch. Wired and on is the shared ledger, one
        connection for the process. Never wired at all — a script, a test,
        a tool run outside an engine — gets a standalone ledger, exactly
        what every such caller did before this existed: the switch belongs
        to the engine, and there is no engine here to have flipped it.
        """
        if self._wired:
            return self._ledger
        db_path = getattr(settings, "db_path", None)
        return SpineUsage(db_path) if db_path else None

    def health(self) -> Health:
        if not self._wired:
            return Health(
                declared=True,
                wired=False,
                reachable=False,
                detail="двигун у цьому процесі не збирався",
            )
        if not self._enabled:
            return Health.off(self.cost)
        if self._ledger is None:  # pragma: no cover - wire() cannot leave this
            return Health(True, False, False, "леджер не збудувався")
        # SpineUsage swallows a failed connect and carries on with no
        # database — every write then returns quietly, and the dashboard
        # reads $0.00 in good faith. That is the silent failure this
        # column exists for.
        db = self._ledger._db
        if db is None:
            return Health(
                True, True, False, f"база недоступна: {self._ledger.db_path}"
            )
        try:
            db.execute("SELECT 1 FROM usage_events LIMIT 1").fetchall()
        except Exception as exc:  # noqa: BLE001 - the error is the answer
            return Health(True, True, False, f"usage_events не читається: {exc}")
        return Health.working()


PLUGIN = UsagePlugin()


def ledger_for(settings: Any) -> SpineUsage | None:
    """Module-level door for writers outside the composition root."""
    return PLUGIN.ledger_for(settings)


@contextmanager
def counting(settings: Any) -> Iterator[SpineUsage | None]:
    """The ledger to write one call into, or None to count nothing.

    The form every writer outside the composition root should use. A
    standalone ledger — the no-engine case of ``ledger_for`` — owns a
    SQLite connection, and a writer that asks once per call must not leave
    one behind per call; the shared ledger belongs to the engine, which
    closes it at shutdown.
    """
    ledger = PLUGIN.ledger_for(settings)
    try:
        yield ledger
    finally:
        if ledger is not None and ledger is not PLUGIN._ledger:
            ledger.close()


__all__ = ["PLUGIN", "SpineUsage", "UsagePlugin", "counting", "ledger_for"]
