"""Distinguish acknowledgements from interruptions, in English.

During the assistant's response, when speech is detected:
- If the person has been speaking longer than INTERRUPT_HOLD_MS, cut in
  at once without waiting for the transcript — it is a real interruption.
- If shorter: listen to the words. A backchannel («yeah», «ok», «got it»)
  means keep going. A negation or stop word («no», «stop», «enough»)
  means cancel. Anything else means interrupt. Nothing spoken (silence,
  breath) means resume.

The rules are the same as in ``uk.py``; only the words differ. What a
language hears is a table of its own, never a translation of another's.
"""

from __future__ import annotations

import re
import unicodedata


# How long speech must last while the assistant speaks before we cut in
# without waiting for transcription (the same measurement placeholder as
# in ``uk.py``).
INTERRUPT_HOLD_MS: int = 600

# Acknowledgements that mean "go on". Punctuation and extra spaces are
# stripped before matching.
_BACKCHANNEL_WORDS: frozenset[str] = frozenset(
    {
        "yeah",
        "yeah yeah",
        "yep",
        "yup",
        "uh-huh",
        "uh huh",
        "ok",
        "okay",
        "ok ok",
        "mhm",
        "mm",
        "mmm",
        "mm-hmm",
        "right",
        "right right",
        "got it",
        "gotcha",
        "i see",
        "i hear you",
    }
)

# Words that mean "stop": interruptions that cancel the response.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "stop",
        "enough",
        "quiet",
        "no",
    }
)

# Negations are never acknowledgements, whatever they are next to.
_NEGATIONS: frozenset[str] = frozenset(
    {"no", "not", "nope", "nah", "never", "don't", "dont", "can't", "cannot"}
)

_PUNCT_EDGES = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)
_SPACES = re.compile(r"\s+")
_REPEATED_LETTERS = re.compile(r"([a-z])\1{2,}", re.UNICODE)
# The recogniser may return a typographic apostrophe; both spell "I'll".
_APOSTROPHE = re.compile("[\u2019]")
_WORD = re.compile(r"[\w'\u2019]+", re.UNICODE)


def _normalise(text: str) -> str:
    """Lowercase, strip decoration and punctuation, collapse spaces,
    collapse repeated letters (mmmm -> mm).
    """
    out = unicodedata.normalize("NFKC", text).lower()
    out = _APOSTROPHE.sub("'", out)
    out = _SPACES.sub(" ", out).strip()
    out = _PUNCT_EDGES.sub("", out)
    out = _SPACES.sub(" ", out).strip()
    # Collapse repeated letters to pairs (mmmm -> mm, okkk -> okk)
    out = _REPEATED_LETTERS.sub(r"\1\1", out)
    return out


def is_backchannel(text: str) -> bool:
    """True when the text is a short acknowledgement meaning 'go on'.

    Backchannels are normalised (lowercase, stripped, collapsed). Must be
    3 words or fewer. Must not contain negations (a "no" is a command,
    not an acknowledgement).
    """
    raw = (text or "").strip()
    if not raw:
        return False

    norm = _normalise(raw)
    if not norm:
        return False

    # Must be 3 words or fewer.
    words = norm.split()
    if len(words) > 3:
        return False

    # Check if the normalised text is a known backchannel word or phrase.
    if norm not in _BACKCHANNEL_WORDS:
        return False

    # Negations are not backchannels — they mean stop.
    if any(word in _NEGATIONS for word in words):
        return False

    return True


def is_stop_word(text: str) -> bool:
    """True when the text is a command to stop speaking.

    Stop words are explicit halt commands («stop», «enough», «quiet») and
    the negation «no».
    """
    raw = (text or "").strip()
    if not raw:
        return False

    norm = _normalise(raw)
    if not norm:
        return False

    return norm in _STOP_WORDS


_RECALL_CUES: tuple[str, ...] = (
    "what did i say", "what did i tell", "what do you remember", "do you remember",
    "you remember", "do you recall", "remind me", "remind",
    "recall", "last time", "earlier", "yesterday", "the day before", "when i",
)


def recall_cue(text: str) -> bool:
    """Whether the person is asking to remember something said before."""
    said = _normalise(text or "")
    return any(cue in said for cue in _RECALL_CUES)


def decide(text: str, *, speech_ms: float) -> str:
    """Decide what to do when the person speaks while the assistant speaks.

    Returns one of: "interrupt" (cut in at once), "resume" (keep going),
    "cancel" (abandon the response).

    The decision logic:
    1. If the person has been speaking >= INTERRUPT_HOLD_MS, interrupt
       at once (it is a real interruption).
    2. Else if the text is a backchannel, resume (quick acknowledgement).
    3. Else if the text is a stop word, cancel (explicit halt).
    4. Else if the text is empty or whitespace, resume (noise/breath).
    5. Else interrupt (anything else is an interruption).
    """
    if speech_ms >= INTERRUPT_HOLD_MS:
        return "interrupt"

    norm_text = _normalise((text or "").strip())

    if is_backchannel(text):
        return "resume"

    if is_stop_word(text):
        return "cancel"

    # Nothing was said (empty or whitespace-only after normalisation).
    if not norm_text:
        return "resume"

    # Anything else is an interruption.
    return "interrupt"


# Words that call work off, said as a turn of their own. Narrower than
# the stop words above: «no» or «quiet» over the speaker stop the voice,
# but said into silence «no» is an answer to a question, not an order.
_HALT_WORDS: frozenset[str] = frozenset(
    {
        "stop", "cancel", "halt", "abort", "enough", "quit", "silence",
        "quiet", "pause", "cease",
    }
)
# Words that may stand around a halt word without making the turn
# anything else: «stop everything», «cancel all the work now», «just stop».
# «Stop the music» is not a command to the assistant about its own work,
# and «music» is not here.
_HALT_FILL: frozenset[str] = frozenset(
    {
        "it", "that", "this", "all", "everything", "now", "please", "just",
        "right", "the", "work", "task", "tasks", "agent", "agents", "for",
        "good", "already", "of", "ok", "okay", "and", "so", "there", "hey",
    }
)


def is_halt(text: str, names: frozenset[str] = frozenset()) -> bool:
    """True when the whole turn is a command to stop: «stop», «Doka, stop»,
    «stop, stop». The assistant's own name is not a word of it."""
    words = [w for w in _WORD.findall(_normalise(text or "")) if w not in names]
    return (
        0 < len(words) <= 6
        and any(w in _HALT_WORDS for w in words)
        and all(w in _HALT_WORDS or w in _HALT_FILL for w in words)
    )


# A yes or a no to a question the assistant asked aloud. Caught here, in
# code: whether the person allowed something must not be a model's guess.
_YES: frozenset[str] = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright", "allow",
     "allowed", "permit", "permitted", "agree", "agreed", "absolutely",
     "definitely", "fine", "please", "go", "ahead", "do", "do it",
     "of course", "course"}
)
_NO: frozenset[str] = frozenset(
    {"no", "nope", "nah", "not", "never", "don't", "dont", "stop", "cancel",
     "deny", "denied", "forbid", "forbidden"}
)


# Words said on the way to the answer: «well, sure» is a yes.
_FILLER: frozenset[str] = frozenset(
    {"well", "um", "uh", "er", "like", "just", "oh", "so", "then", "hmm"}
)


def consent(text: str, names: frozenset[str] = frozenset()) -> bool | None:
    """True for a yes, False for a no, None when it is neither.

    Only a short answer counts: «yes, and also check the mail» is a new
    request and has to be heard as one.
    """
    norm = _normalise(text or "")
    words = [w for w in _WORD.findall(norm) if w not in names and w not in _FILLER]
    if not 0 < len(words) <= 3:
        return None
    said = " ".join(words)
    if said in _NO or any(w in _NO for w in words):
        return False
    if said in _YES or all(w in _YES for w in words):
        return True
    return None


def tokens(text: str) -> list[str]:
    """The words of a phrase, lower-cased: for telling whether two
    requests are about the same thing."""
    return _WORD.findall((text or "").lower())


__all__ = [
    "INTERRUPT_HOLD_MS",
    "consent",
    "decide",
    "is_backchannel",
    "is_halt",
    "is_stop_word",
    "recall_cue",
]

from heare.language.promises_en import claims_done, promises_work  # noqa: E402

__all__ += ["claims_done", "promises_work", "tokens"]
