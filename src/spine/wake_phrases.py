"""What counts as being addressed.

An always-on assistant that answers everything answers the room. Observed
live: a podcast playing nearby produced a turn every few seconds, each
starting a model call that the next sentence interrupted, so it never
finished a reply and never stopped trying.

The gate is on *acting*, not on hearing. Speech is still transcribed
while asleep, which is what makes "listen and remember, answer when
asked" possible rather than a contradiction.

Matching is exact-word, case-insensitive, over the final transcript — so
the list has to contain what speech recognition actually produces, not
what the name is spelled like. Groq's Whisper heard "Doka" as "докер",
"дока" and "Дока" in a single session; a wake word only works if it is
robust to that.

Lives in the spine's own tree: the phrase table is framework-free, but
importing it from the old engine's package dragged in its __init__, and
with it pipecat. The spine loaded this file by path to dodge that; now
it just imports it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("heare.wake")

# Variants Whisper produced for this assistant's name, plus the ones any
# Ukrainian speaker will say when addressing it. Cheap to include, and
# each missing one is a moment where the user is ignored.
_KNOWN_VARIANTS: dict[str, tuple[str, ...]] = {
    "doka": ("doka", "дока", "докер", "дока́", "доко", "доку"),
    "гава": ("гава", "гаво", "гави", "hava", "гаваа"),
}


def _variants(word: str) -> list[str]:
    """Every spelling worth listening for, given one name."""
    key = word.strip().lower()
    out = list(_KNOWN_VARIANTS.get(key, ()))
    if key and key not in out:
        out.insert(0, key)
    return out


def _name_from_persona(persona: str) -> str:
    """Pull the assistant's own name out of its rendered persona.

    The identity is generated at first run, so the name is not a constant
    anyone can hard-code. "You are Doka 🎧 — ..." is the shape it takes.
    """
    match = re.search(r"You are ([^\s—,.]+)", persona or "")
    return match.group(1).strip() if match else ""


def wake_phrases(settings: Any, persona: str = "") -> list[str]:
    """The phrases that mean "I am talking to you".

    Both the configured wake word and the assistant's own name: people
    call it by name, and the wake word is a separate setting that may
    never have been changed from its default.
    """
    phrases: list[str] = []
    for source in (_name_from_persona(persona), getattr(settings, "wake_word", "")):
        for variant in _variants(source):
            if variant and variant not in phrases:
                phrases.append(variant)

    if not phrases:  # never gate on an empty list — it would block everything
        logger.warning("wake: no phrases resolved; falling back to 'гава'")
        phrases = ["гава"]
    return phrases


__all__ = ["wake_phrases"]
