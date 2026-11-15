"""A plan before the work: what the hands would do, without doing it.

The person says «спершу покажи план»; the hands run one turn as the agent
that only looks. What comes back is words, and nothing on disk moves.
"""

from __future__ import annotations

from typing import Callable

from heare.core.forms import Task
from heare.core.i18n import _
from heare.tools.hands import Hand, choose


async def run(
    start: Callable[[str, str], Task],
    hands: list[Hand],
    arguments: dict,
) -> str:
    """Ask the hands for a plan. ``start`` takes the task and the hand's id
    and returns at once; the plan arrives as its own message."""
    request = str(arguments.get("task") or "").strip()
    if not request:
        return _("Nothing to plan: the task was empty.")
    hand, why_not = choose(hands, str(arguments.get("hand") or ""))
    if not hand:
        return why_not
    start(request, hand)
    return _("Asked the hands for a plan.")
