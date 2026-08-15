"""What Whisper says when nobody is speaking.

Trained on captioned video, Whisper answers silence and noise with the
furniture of that world: closing credits, «Дякую за перегляд!», a bare
«Музика», a lone «*тиша*». On this deployment's own database those
phrases are the most frequent "user utterances" on record — 398 bare
«Дякую.», 77 «*тиша*», 40 sign-offs — which is what poisoned meeting
transcripts and filled the assistant's context with polite ghosts.

Two tiers, because the cost of the two mistakes is not the same:
phrases that are never real speech are dropped outright, while a bare
courtesy («дякую») is dropped only when there is nobody to thank —
i.e. the assistant has not just spoken. Real gratitude to a real answer
survives; gratitude to an empty room does not.
"""
from __future__ import annotations

import re
import unicodedata

# Never real voice input: subtitle-world artifacts and non-speech markers.
_ALWAYS: frozenset[str] = frozenset(
    {
        "дякую за перегляд",
        "дякую за увагу",
        "спасибо за просмотр",
        "спасибо за внимание",
        "thanks for watching",
        "thank you for watching",
        "субтитри",
        "субтитри створено спільнотою",
        "субтитрував",
        "редактор субтитрів",
        "субтитры",
        "редактор субтитров",
        "продовження далі",
        "продолжение следует",
        "to be continued",
        "тиша",
        "мовчання",
        "музика",
        "музыка",
        "music",
        "аплодисменти",
        "аплодисменты",
        "applause",
        "сміх",
        "смех",
        "laughter",
        "кінець",
        "конец",
        "the end",
        "you",
    }
)

# Real words, but meaningless alone when the assistant has said nothing.
_COURTESY: frozenset[str] = frozenset(
    {
        "дякую",
        "дякую дякую",
        "спасибі",
        "спасибо",
        "будь ласка",
        "будьласка",
        "пожалуйста",
        "thank you",
        "thanks",
        "ага",
        "угу",
        "ммм",
        "м-м-м",
        "хм",
        "ок",
        "окей",
        "ok",
        "okay",
    }
)

# Subtitle credits arrive with a name attached («Редактор субтитров
# А.Семкин»), so these match on the opening words rather than exactly.
_PREFIXES: tuple[str, ...] = (
    "редактор субтитр",
    "субтитр",
    "дякую за перегляд",
    "спасибо за просмотр",
    "продовження дал",
    "продолжение след",
    "переклад субтитр",
)

_BRACKETED = re.compile(r"^[\[\(\*<]+.*[\]\)\*>]+$")
# Two words welded by punctuation with no space («Будьласка,бро.») —
# letters on both sides, so numbers («287,000») and times («12:30»)
# stay whole.
_GLUED_PUNCT = re.compile(r"[^\W\d_][,;:][^\W\d_]", re.UNICODE)
_PUNCT_EDGES = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)
_SPACES = re.compile(r"\s+")

# Longer than this without a single space is not something a person said:
# it is Whisper gluing a phrase together («Будьласка,бро.» — 45 rows).
_GLUED_MIN_CHARS = 14


def _normalise(text: str) -> str:
    """Lowercase, strip decoration and punctuation, collapse spaces."""
    out = unicodedata.normalize("NFKC", text).lower()
    out = _SPACES.sub(" ", out).strip()
    out = _PUNCT_EDGES.sub("", out)
    return _SPACES.sub(" ", out).strip()


def is_junk(text: str, *, agent_spoke_recently: bool = False) -> bool:
    """True when this transcript should never have been a turn.

    ``agent_spoke_recently`` keeps courtesy replies alive: «дякую» right
    after an answer is a person being polite, the same word into silence
    is Whisper filling a gap.
    """
    raw = (text or "").strip()
    if not raw:
        return True

    norm = _normalise(raw)
    if not norm:
        return True  # decoration only: "***", "[...]", "..."

    if norm in _ALWAYS or norm.startswith(_PREFIXES):
        return True

    # A whole utterance wrapped in brackets or asterisks is a caption
    # marker, not speech: «*тиша*», «[музика]», «(applause)».
    if _BRACKETED.match(raw.strip()):
        return True

    if " " not in norm and (
        len(norm) >= _GLUED_MIN_CHARS or _GLUED_PUNCT.search(norm)
    ):
        return True

    if not agent_spoke_recently and norm in _COURTESY:
        return True

    return False


__all__ = ["is_junk"]
