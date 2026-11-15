"""Which hand an order goes to: the one named, or the only one there.

An order is not work to route: «відкоти» and «спершу план» go to the hands
that are here, and two hands with no name cannot answer for each other.
"""

from __future__ import annotations

from heare.core.i18n import _

# What the brain knows of a hand: its id, its channel, its machine, its line.
Hand = tuple[str, str, str, str]


def choose(hands: list[Hand], wanted: str) -> tuple[str, str]:
    """The hand's id, or ('', why not) when there is no single answer."""
    if not hands:
        return "", _("No hands are connected now.")
    wanted = wanted.strip()
    if wanted:
        if any(pid == wanted for pid, *_rest in hands):
            return wanted, ""
        return "", _("No such hands: {hand}.").format(hand=wanted)
    if len(hands) != 1:
        listing = ", ".join(f"{about} ({pid})" for pid, _name, _machine, about in hands)
        return "", _("Which hands? {hands}.").format(hands=listing)
    return hands[0][0], ""
