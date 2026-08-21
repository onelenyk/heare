"""What the worker is not allowed to touch right now.

This is what is left of `src/agent/modes.py` after the modes were taken
out, and it is the only part of that module anything ever used.

Why the modes went
------------------
A mode was a global adjective on the whole system: `focus`, `silent`,
`meeting`. It meant nothing on its own — it existed only as differences
in each layer that has behaviour, so every layer had to go and read a
global flag and decide for itself what to do about it. Seven levers
across nine places, and a layer that forgot silently did nothing.

By 21 August all of it was gone but the declaration. Three levers had
lost their executors when the old engine and its pipeline were deleted;
two had never reached the spine at all; and the flag itself never
arrived, because the object holding it was not wired into the loop. The
registry survived every dead-code sweep only because three files still
imported it — for roles, not for modes.

Roles won for one reason: a role has a lifetime. It starts on a spoken
phrase, holds, ends on another, and leaves a document. So there is
always one object to ask what is in force, and the question is asked in
one place — which is this file.

The whole gate
--------------
A policy names the globs it forbids. Everything else is allowed. There
is no escape-hatch tool any more: a mode needed one because a
restrictive mode could otherwise lock you out of changing it, whereas
the way out of a role is to say «закінчили», and no tool is involved.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass

logger = logging.getLogger("heare.tool_gate")


@dataclass(frozen=True)
class ToolPolicy:
    """What is forbidden while something is in force, and under whose
    name to say so when it is refused.

    ``voice_muted`` rides along because it is the same question asked of
    the same object — a role with `channel: log` records without
    speaking — and splitting them would mean two lookups of one fact.
    """

    name: str = "ambient"
    denied_tool_patterns: tuple[str, ...] = ()
    voice_muted: bool = False


# What runs when nothing is in force. Named rather than None so callers
# never have to write `if policy is None` — a gate that can be skipped by
# forgetting a check is not a gate.
OPEN = ToolPolicy()


def is_tool_allowed(policy: ToolPolicy | None, tool_name: str) -> bool:
    """True if *tool_name* may run under *policy*."""
    if policy is None:
        return True
    return not any(
        fnmatch.fnmatchcase(tool_name, pattern)
        for pattern in policy.denied_tool_patterns
    )


def gate_refusal(session_state: object | None, tool_name: str) -> dict | None:
    """The execution-time gate, shared by both tool-handler chokepoints.

    Returns a refusal in the tool-result shape when the call is denied,
    or None when it is allowed. Both handlers call this rather than
    keeping their own copy, because two copies of an allow/deny rule
    drift, and the drift is invisible until something forbidden runs.
    """
    if session_state is None:
        return None
    policy = getattr(session_state, "policy", None)
    if is_tool_allowed(policy, tool_name):
        return None
    logger.info("tool_gate: blocked %r during %s", tool_name, policy.name)
    return {
        "success": False,
        "output": "",
        "error": f"{tool_name} is unavailable during {policy.name}",
    }


__all__ = ["OPEN", "ToolPolicy", "gate_refusal", "is_tool_allowed"]
