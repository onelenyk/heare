"""A reply that promises work it did not start, in English.

The same net as ``promises.py``, in the words an English reply uses:
"I'll check", "looking", "on it", "let me see", "one second", "I'll find
it". A short reply in the first person present or future, or asking for a
moment, with no tool call beside it, is treated as the delegation the
model meant.

The error is asymmetric, which is why the net is wide. Delegating a turn
that needed nothing costs one extra line from the hands; missing one
costs the answer.
"""

from __future__ import annotations

import re

_PROMISE = re.compile(
    r"(?:"
    # First person future: I'll check, I'll look, I'll find it.
    r"\bi['\u2019]ll\s+"
    r"(?:check|look|see|find|search|open|run|start|set|add|update|create|"
    r"write|fix|do|handle|get|grab|pull|try)\b"
    # First person present: I'm checking, I am looking.
    r"|\bi['\u2019]m\s+"
    r"(?:checking|looking|searching|opening|running|starting|setting|adding|"
    r"updating|creating|writing|fixing|doing|handling|getting|trying)\b"
    r"|\bi\s+am\s+"
    r"(?:checking|looking|searching|opening|running|starting|setting|adding|"
    r"updating|creating|writing|fixing|doing|handling|getting|trying)\b"
    # Asking for a moment.
    r"|\blet\s+me\s+"
    r"(?:check|look|see|find|search|try|open|run|set|add|update|create|"
    r"write|fix|handle|get)\b"
    r"|\b(?:one|just\s+a|a|wait\s+a|give\s+me\s+a)\s+"
    r"(?:second|sec|moment|minute)\b"
    # Bare present participles: "Looking into it."
    r"|\b(?:checking|looking|searching|opening|running|setting|adding|"
    r"updating|creating|writing|fixing|handling|getting)\b"
    # "On it." is the shortest promise of all.
    r"|\bon\s+it\b"
    r")",
    re.IGNORECASE,
)

# Past the length of an acknowledgement, a reply is an answer that
# happens to contain one of the words above, not a promise standing in
# for one. The same guard as ``promises.py``.
_MAX_PROMISE_CHARS = 120


def promises_work(reply: str) -> bool:
    """True when `reply` announces work in place of doing it."""
    text = (reply or "").strip()
    return bool(text) and len(text) <= _MAX_PROMISE_CHARS and bool(_PROMISE.search(text))


# A reply that says something was drawn or changed: "Updated the
# weather", "Done.", "Here it is." Unlike a promise of work, this is
# never handed to an executor: it is asked for again, and that is all.
_CLAIM = re.compile(
    r"\b(?:"
    r"done|done\s+it|updated|added|shown|created|fixed|changed|removed|"
    r"deleted|set|finished|completed|ready|all\s+set|here\s+it\s+is|"
    r"here\s+you\s+go|drew|drawn|displayed|saved|renamed|moved|rebuilt"
    r")\b",
    re.IGNORECASE,
)


def claims_done(reply: str) -> bool:
    """True when `reply` says a thing was shown or changed."""
    text = (reply or "").strip()
    return bool(text) and len(text) <= _MAX_PROMISE_CHARS and bool(_CLAIM.search(text))
