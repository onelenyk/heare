"""Auto-extract memories from conversation text using regex heuristics.

Zero LLM calls — pure regex pattern matching. Extracts facts, preferences,
decisions, and events from user utterances and agent inferences. Designed
to run as a fire-and-forget background task after each turn.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.memory.base import MemoryEntry, MemoryType

if TYPE_CHECKING:
    from src.memory.base import MemoryBackend

# ── Extraction patterns ──────────────────────────────────────────────
# Each pattern is (regex, memory_type). The first capture group is the value.

_PATTERNS_UK: list[tuple[re.Pattern, MemoryType]] = [
    # Name / identity
    (
        re.compile(r"мене звати\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (re.compile(r"я\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE), MemoryType.FACT),
    # Preferences
    (
        re.compile(
            r"я\s+(?:люблю|обожнюю|полюбляю)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"мені\s+(?:подобається|до вподоби)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"мій\s+(?:улюблений|улюблена|улюблене)\s+(\S[\s\S]{0,40}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    # Decisions
    (
        re.compile(
            r"я\s+(?:вирішив|вирішила|вирішили)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.DECISION,
    ),
    (
        re.compile(
            r"(?:домовились|вирішено|зроблено)\s*[:—–-]?\s*(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.DECISION,
    ),
    # Location
    (
        re.compile(
            r"я\s+(?:живу|мешкаю|знаходжусь)\s+(?:в|у)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
    # Job / role
    (
        re.compile(r"я\s+(?:працюю|є)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
]

_PATTERNS_EN: list[tuple[re.Pattern, MemoryType]] = [
    (
        re.compile(r"my name is\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (
        re.compile(r"i(?:'m| am)\s+(\S[\s\S]{0,40}?)[.!?]?\s*$", re.IGNORECASE),
        MemoryType.FACT,
    ),
    (
        re.compile(
            r"i\s+(?:like|love|enjoy|prefer)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"my (?:favourite|favorite)\s+\w+\s+is\s+(\S[\s\S]{0,40}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.PREFERENCE,
    ),
    (
        re.compile(
            r"i\s+(?:decided|chose|picked)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$", re.IGNORECASE
        ),
        MemoryType.DECISION,
    ),
    (
        re.compile(
            r"i\s+(?:live|reside)\s+(?:in|at)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
    (
        re.compile(
            r"i\s+(?:work|am)\s+(?:as|a|an)\s+(\S[\s\S]{0,60}?)[.!?]?\s*$",
            re.IGNORECASE,
        ),
        MemoryType.FACT,
    ),
]


def extract_memories(text: str) -> list[MemoryEntry]:
    """Extract MemoryEntry objects from a single text string.

    Tests all UK + EN patterns. Returns empty list if nothing matches.
    Each match becomes a MemoryEntry with source="auto_extracted".
    """
    results: list[MemoryEntry] = []
    seen: set[str] = set()

    for patterns in (_PATTERNS_UK, _PATTERNS_EN):
        for pat, mem_type in patterns:
            m = pat.search(text)
            if not m:
                continue
            value = m.group(1).strip().rstrip(".!?,;:")
            if not value or len(value) < 2:
                continue
            # Dedup within this extraction batch
            dedup_key = f"{mem_type.value}:{value.lower()}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            results.append(
                MemoryEntry(
                    id="",
                    type=mem_type,
                    content=value,
                    source="auto_extracted",
                    confidence=0.7,  # lower confidence for auto-extracted
                )
            )

    return results


async def extract_and_store(backend: "MemoryBackend", text: str) -> int:
    """Extract memories from text and store them via the backend.

    Checks for near-duplicates before storing (simple content match).
    Returns number of new memories stored.
    """
    extracted = extract_memories(text)
    if not extracted:
        return 0

    stored = 0
    for entry in extracted:
        # Check for existing similar memories (simple content match)
        existing = await backend.search(entry.content, limit=1)
        if existing:
            continue  # skip near-duplicate

        await backend.store(entry)
        stored += 1

    return stored


__all__ = ["extract_memories", "extract_and_store"]
