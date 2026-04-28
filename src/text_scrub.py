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

# Phase 2.2 US-P2.2-07 + Phase AH2-02: post-parser TTS scrubber.
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AH2-02: Claude Code's bash tool emits this literal marker when a
    # command has no stdout. Drop the whole phrase (parens included)
    # so TTS never says any fragment of it. Must run BEFORE the
    # standalone ``bash`` word-boundary rule below or only "Bash"
    # would be stripped.
    (re.compile(r"\(\s*Bash completed with no output\s*\)", re.IGNORECASE), ""),
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


def scrub_tts_text(text: str) -> str:
    """Strip tool-name literals and JSON fragments before TTS synthesis."""
    out = text
    for pat, repl in _SCRUB_PATTERNS:
        out = pat.sub(repl, out)
    return out.strip()


__all__ = ["scrub_tts_text"]
