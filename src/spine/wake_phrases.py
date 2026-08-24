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

# What the recogniser actually produced for these names, gathered by
# reading transcripts. NOT a list of spellings — «докер» is not a form of
# «Дока» in any language, it is what Groq's Whisper returns for it, and
# the module docstring above is the reason that distinction matters.
#
# Keyed by name, and a name that is not here is not a bug: `_forms`
# below covers the ordinary case. This is for the mishearings, which can
# only be learned by looking.
_MISHEARD: dict[str, tuple[str, ...]] = {
    "дока": ("доку", "дока́", "докер", "доко"),
    "doka": ("доку", "дока́", "докер", "доко"),
    "гава": ("гаво", "гави", "гаваа"),
}

# Latin letters as this deployment's names actually transliterate. Only
# the ones that occur: a generated name is a short word, not a passport.
_LATIN = str.maketrans({
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "г", "i": "і", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
    "y": "и", "z": "з",
})


def _forms(name: str) -> list[str]:
    """The name as a person addressing it would actually say it.

    Ukrainian has a vocative and people use it: you call «Доко», not
    «Дока». The gate matches whole words, so without the vocative the
    most natural way to address the assistant is the one way it does not
    answer to.

    Only the vocative, and only for the -а ending. Wider declension is
    where this stops being safe: the genitive of «Гава» is «гави», which
    is also an ordinary Ukrainian word, and a wake phrase that is also a
    word is a podcast starting a conversation every few seconds.
    """
    out = [name]
    if len(name) > 2 and name.endswith("а"):
        out.append(name[:-1] + "о")
    return out


def _variants(word: str) -> list[str]:
    """Every spelling worth listening for, given one name.

    Three sources, in order of how much they are trusted: the name
    itself, the forms a person says it in, and the shapes it has been
    misheard as. A Latin name is also written the way it is spoken —
    identity.json has held «Doka» while everyone in the room said
    «Дока».
    """
    key = word.strip().lower()
    if not key:
        return []
    out: list[str] = []
    written = [key]
    if key.isascii():
        cyrillic = key.translate(_LATIN)
        if cyrillic != key:
            written.append(cyrillic)
    for spelling in written:
        for form in _forms(spelling):
            if form not in out:
                out.append(form)
    for heard in _MISHEARD.get(key, ()):
        if heard not in out:
            out.append(heard)
    return out


def _name_from_persona(persona: str) -> str:
    """Pull the assistant's own name out of its rendered persona.

    Kept for the one caller that has a persona string and no identity
    file. Production hands the name in directly: the composition root
    used to read identity.json, wrap the name in «You are {name}» and
    hand it here to be regexed back out — two modules agreeing on a
    format neither of them writes.
    """
    match = re.search(r"You are ([^\s—,.]+)", persona or "")
    return match.group(1).strip() if match else ""


def wake_phrases(settings: Any, persona: str = "", name: str = "") -> list[str]:
    """The phrases that mean "I am talking to you".

    The name comes first and is the assistant's own, from identity.json.
    `settings.wake_word` is an *addition* — one more thing to answer to —
    and not a second name: it used to be both, and because it defaults to
    «гава» while the generated name was «Doka», the startup greeting
    announced a name the assistant did not believe it had. Three days of
    a person saying «Привіт!» into a room that was listening for
    something else.
    """
    phrases: list[str] = []
    for source in (name or _name_from_persona(persona),
                   getattr(settings, "wake_word", "")):
        for variant in _variants(source):
            if variant and variant not in phrases:
                phrases.append(variant)

    if not phrases:  # never gate on an empty list — it would block everything
        logger.warning("wake: no phrases resolved; falling back to 'гава'")
        phrases = ["гава"]
    return phrases


def own_name(settings: Any) -> str:
    """The assistant's own name, from the one place that owns it.

    Every consumer that needs the name — the persona the model reads, the
    startup greeting, the wake gate — reads it through here, so they
    cannot disagree. They did: the greeting had its own source and said
    «Гава на зв'язку» while the model introduced itself as «Дока».

    Settings that carry no `identity_file` have no identity: this does
    NOT reach into the home directory for one. The real Settings always
    has the field, so the only callers without it are hand-made objects
    in tests, and a fallback to `~/.heare` would make the suite read the
    developer's own assistant. That leak is not hypothetical — two e2e
    scenarios went red the afternoon a switch was flipped in a personal
    config.toml.
    """
    import json
    from pathlib import Path

    path = getattr(settings, "identity_file", None)
    name = ""
    if path is not None:
        try:
            name = json.loads(Path(path).read_text("utf-8")).get("name") or ""
        except Exception:  # noqa: BLE001 — a missing identity is not a crash
            name = ""
    return str(name).strip() or str(getattr(settings, "wake_word", "") or "").strip()


__all__ = ["own_name", "wake_phrases"]
