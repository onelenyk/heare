"""Putting back what the last piece of work changed.

The hands keep the server's own snapshot of a task, so undoing is one
order to them — not work for a model: the words of the person ("відкоти",
"поверни як було") reach a verb, and the verb reaches the hands that did
the work.
"""

from __future__ import annotations

from typing import Callable

from heare.core.forms import Task
from heare.core.i18n import _
from heare.tools.hands import Hand, choose


async def run(
    start: Callable[[str], Task],
    hands: list[Hand],
    arguments: dict,
) -> str:
    """Order the hands to undo. ``start`` takes the hand's id and returns at
    once; the answer arrives as its own message, as any work does."""
    hand, why_not = choose(hands, str(arguments.get("hand") or ""))
    if not hand:
        return why_not
    start(hand)
    return _("Asked the hands to put it back.")
