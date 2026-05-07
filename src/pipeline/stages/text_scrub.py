"""TTS text scrubbing — moved from src/generator.py for PH2-06.

Defense-in-depth scrubber that strips tool-name literals, JSON
fragments, and Claude Code status markers before TTS synthesis. Used
by both the legacy generator path and main.py's action-result speech
path; once PH2-06 deletes generator.py the scrubber needs a stable
home so the surviving callers still resolve.

Pure / synchronous / no Pipecat dependency.
"""
from __future__ import annotations

import re

from src.agent.tools.registry import TOOLS


# Tool names the LLM might emit as plain text instead of calling. Pulled
# from the live registry so additions like ``create_skill`` are caught
# automatically — keeps the scrubber and the registry from drifting.
_TOOL_NAMES = sorted(TOOLS.keys(), key=len, reverse=True)
_TOOL_NAME_ALT = "|".join(re.escape(n) for n in _TOOL_NAMES)

# Phase 2.2 US-P2.2-07 + Phase AH2-02: post-parser TTS scrubber.
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AH2-02: Claude Code's bash tool emits this literal marker when a
    # command has no stdout. Drop the whole phrase (parens included)
    # so TTS never says any fragment of it. Must run BEFORE the
    # standalone ``bash`` word-boundary rule below or only "Bash"
    # would be stripped.
    (re.compile(r"\(\s*Bash completed with no output\s*\)", re.IGNORECASE), ""),
    # ``tool_name: arg`` narration pattern — observed in daemon logs as
    # ``bash: system_profiler SPHardwareDataType``. The model wrote the
    # call as text instead of invoking the function. Drop the whole
    # segment up to the next sentence boundary so TTS doesn't read
    # commands aloud.
    (
        re.compile(
            rf"(?<![\w])(?:{_TOOL_NAME_ALT})\s*:\s*[^\n.!?]+[.!?\n]?",
            re.IGNORECASE,
        ),
        "",
    ),
    # Drop standalone bash/Bash token (word boundary) — but NOT "bashful".
    (re.compile(r"(?<![\w])bash(?![\w])", re.IGNORECASE), ""),
    # JSON fragments that clearly leaked from an intent tag.
    (re.compile(r'\{"tool"\s*:\s*"[^"]*"\s*,\s*"args"\s*:\s*"[^"]*"\s*\}'), ""),
    (re.compile(r'"tool"\s*:\s*"[^"]*"'), ""),
    (re.compile(r'"args"\s*:\s*"[^"]*"'), ""),
    (re.compile(r"</?\s*intent\s*>", re.IGNORECASE), ""),
    # Cleanup: collapse 2+ spaces left behind, trim.
    (re.compile(r"\s{2,}"), " "),
]


# Whole-message tool-name match. Run separately from the inline scrubber
# because we want different semantics: if the entire scrubbed message is
# just a tool name, drop it to silence rather than letting TTS speak it.
_TOOL_NAME_ONLY_RE = re.compile(
    rf"^\s*(?:{_TOOL_NAME_ALT})\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def scrub_tts_text(text: str) -> str:
    """Strip tool-name literals and JSON fragments before TTS synthesis.

    If the entire (post-scrub) message is just a tool name — observed
    in production as the model emitting ``list_tools`` or
    ``list_capabilities`` instead of invoking them — return an empty
    string so TTS produces silence rather than reading the tool name
    aloud.
    """
    if _TOOL_NAME_ONLY_RE.match(text):
        return ""
    out = text
    for pat, repl in _SCRUB_PATTERNS:
        out = pat.sub(repl, out)
    return out.strip()


__all__ = ["scrub_tts_text"]
