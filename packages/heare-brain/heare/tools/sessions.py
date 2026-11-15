"""Going on with work the hands already did, or beside it.

The hands keep every session their agent ever ran, with the agent's own
title for it. So «продовж ту, де був нотпад» is a question to them first
(``recent``) and an order second (``continuing``) — the brain never keeps
a copy of that history.
"""

from __future__ import annotations

from typing import Callable

from heare.core.forms import Task
from heare.core.i18n import _
from heare.tools.hands import Hand, choose


async def recent(
    start: Callable[[str], Task],
    hands: list[Hand],
    arguments: dict,
) -> str:
    """Ask the hands what they did lately. The list arrives as a message."""
    hand, why_not = choose(hands, str(arguments.get("hand") or ""))
    if not hand:
        return why_not
    start(hand)
    return _("Asked the hands what they did lately.")


async def continuing(
    start: Callable[[str, str, str, bool], Task],
    hands: list[Hand],
    arguments: dict,
) -> str:
    """Go on with a named session. ``start`` takes (task, session, hand,
    fork) and returns at once; the result arrives as its own message."""
    request = str(arguments.get("task") or "").strip()
    if not request:
        return _("Nothing to do: the task was empty.")
    session = str(arguments.get("session") or "").strip()
    if not session:
        return _("Which piece of work? Ask what the hands did lately first.")
    hand, why_not = choose(hands, str(arguments.get("hand") or ""))
    if not hand:
        return why_not
    start(request, session, hand, arguments.get("fork") is True)
    return _("Asked the hands to go on with it.")
