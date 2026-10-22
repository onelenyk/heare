"""Distinguish acknowledgements from interruptions.

During the assistant's response, when speech is detected:
- If the user has been speaking longer than INTERRUPT_HOLD_MS, cut in
  at once without waiting for the transcript — it is a real interruption.
- If shorter: listen to the words. A backchannel («ага», «ок», «так»)
  means keep going. A negation or stop word («ні», «стоп», «досить»)
  means cancel. Anything else means interrupt. Nothing spoken (silence,
  breath) means resume.

Two-stage interruption keeps the conversation natural: a quick nod does
not cut off the middle of a sentence, but a long breath or a question
does. The hold time (INTERRUPT_HOLD_MS) is a measurement placeholder —
what matters is that it is long enough to distinguish a human's instant
reaction from their actual interjection.
"""
from __future__ import annotations

import re
import unicodedata


# How long speech must last while the assistant speaks before we cut in
# without waiting for transcription (guess, to be replaced by a real
# measurement when barge-in timing is measured under user load).
INTERRUPT_HOLD_MS: int = 600

# Acknowledgements that mean "go on": Ukrainian first, then English,
# then Russian. These are stripped of punctuation and extra spaces
# for matching.
_BACKCHANNEL_WORDS: frozenset[str] = frozenset(
    {
        "ага",
        "угу",
        "так",
        "так-так",
        "так, так",
        "ок",
        "окей",
        "добре",
        "ясно",
        "зрозуміло",
        "мм",
        "ммм",
        "м-м",
        "ну так",
        "ага, ага",
        "да",
        "понятно",
        "yeah",
        "uh-huh",
        "uh huh",
        "ok",
        "okay",
        "mhm",
        "right",
    }
)

# Words that mean "stop": interruptions that cancel the response.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "стоп",
        "стій",
        "зупинись",
        "замовкни",
        "досить",
        "тихо",
        "stop",
        "хватит",
    }
)

_PUNCT_EDGES = re.compile(r"^[\s\W_]+|[\s\W_]+$", re.UNICODE)
_SPACES = re.compile(r"\s+")
_REPEATED_LETTERS = re.compile(r"([а-яіїєґa-z])\1{2,}", re.UNICODE)


def _normalise(text: str) -> str:
    """Lowercase, strip decoration and punctuation, collapse spaces,
    collapse repeated letters (мммм→мм).
    """
    out = unicodedata.normalize("NFKC", text).lower()
    out = _SPACES.sub(" ", out).strip()
    out = _PUNCT_EDGES.sub("", out)
    out = _SPACES.sub(" ", out).strip()
    # Collapse repeated letters to pairs (мммм → мм, ххх → хх)
    out = _REPEATED_LETTERS.sub(r"\1\1", out)
    return out


def is_backchannel(text: str) -> bool:
    """True when the text is a short acknowledgement meaning 'go on'.

    Backchannels are normalised (lowercase, stripped, collapsed).
    Must be 3 words or fewer. Must not contain verbs, questions, or
    negations (those are commands, not acknowledgements).
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
    if "ні" in norm or "не" in norm or "нет" in norm or "no" in norm:
        return False

    return True


def is_stop_word(text: str) -> bool:
    """True when the text is a command to stop speaking.

    Stop words include negations and explicit halt commands: «стоп»,
    «стій», «зупинись», «замовкни», «досить», «тихо» (Ukrainian),
    «stop» (English), and «хватит» (Russian).
    """
    raw = (text or "").strip()
    if not raw:
        return False

    norm = _normalise(raw)
    if not norm:
        return False

    # Check if it is a known stop word.
    if norm in _STOP_WORDS:
        return True

    # Negations are stop words too.
    if norm in ("ні", "нет", "no"):
        return True

    return False


_RECALL_CUES: tuple[str, ...] = (
    "що я казав", "що я говорив", "що я тобі", "що ти пам", "ти пам",
    "я вже казав", "я вже говорив", "нагадай", "згадай", "пригадай",
    "минулого разу", "раніше", "вчора", "позавчора", "коли я",
)


def recall_cue(text: str) -> bool:
    """Whether the person is asking to remember something said before."""
    said = _normalise(text or "")
    return any(cue in said for cue in _RECALL_CUES)


def decide(text: str, *, speech_ms: float) -> str:
    """Decide what to do when the user speaks while the assistant speaks.

    Returns one of: "interrupt" (cut in at once), "resume" (keep going),
    "cancel" (abandon the response).

    The decision logic:
    1. If the user has been speaking ≥ INTERRUPT_HOLD_MS, interrupt
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


__all__ = [
    "INTERRUPT_HOLD_MS",
    "consent",
    "decide",
    "is_backchannel",
    "is_halt",
    "is_stop_word",
    "recall_cue",
]


# Words that call work off, said as a turn of their own. Narrower than
# the stop words above: «ні» or «тихо» over the speaker stop the voice,
# but said into silence «ні» is an answer to a question, not an order.
_HALT_WORDS: frozenset[str] = frozenset(
    {
        "стоп", "стій", "зупинись", "зупинися", "зупини", "припини",
        "досить", "скасуй", "відбій", "stop", "cancel", "хватит",
        "зупиняй", "зупиніть", "скасовуй", "відміни", "відміняй", "припиняй",
    }
)
# Words that may stand around a halt word without making the turn
# anything else: «зупини все», «скасуй усі справи негайно», «а ну стоп».
# «Зупини музику» is not a command to the assistant about its own work,
# and «музику» is not here.
_HALT_FILL: frozenset[str] = frozenset(
    {
        "все", "усе", "всі", "усі", "всіх", "усіх", "це", "то", "роботу", "роботи",
        "агентів", "агента", "агенти", "справи", "справу", "негайно", "будь",
        "ласка", "поки", "зараз", "там", "їх", "повністю", "взагалі", "а", "ну",
        "і", "й", "та", "вже", "кажу",
    }
)
_WORD = re.compile(r"[\w'ʼ’]+", re.UNICODE)


def is_halt(text: str, names: frozenset[str] = frozenset()) -> bool:
    """True when the whole turn is a command to stop: «стоп», «Дока, стоп»,
    «зупинись, зупинись». The assistant's own name is not a word of it."""
    words = [w for w in _WORD.findall(_normalise(text or "")) if w not in names]
    return (
        0 < len(words) <= 6
        and any(w in _HALT_WORDS for w in words)
        and all(w in _HALT_WORDS or w in _HALT_FILL for w in words)
    )



# A yes or a no to a question the assistant asked aloud. Caught here, in
# code: whether the person allowed something must not be a model's guess.
_YES: frozenset[str] = frozenset(
    {"так", "да", "yes", "ага", "угу", "давай", "добре", "дозволяю", "можна",
     "роби", "ок", "окей", "звісно", "авжеж", "дозволяєм", "дозволяємо",
     "дозволено", "згоден", "згодна", "згода", "погоджуюсь", "звичайно",
     "нехай", "хай"}
)
_NO: frozenset[str] = frozenset(
    {"ні", "нет", "no", "не", "не треба", "не можна", "забороняю", "стоп",
     "скасуй", "не дозволяю"}
)


def consent(text: str, names: frozenset[str] = frozenset()) -> bool | None:
    """True for a yes, False for a no, None when it is neither.

    Only a short answer counts: «так, і ще глянь пошту» is a new request
    and has to be heard as one.
    """
    norm = _normalise(text or "")
    words = [w for w in _WORD.findall(norm) if w not in names]
    if not 0 < len(words) <= 3:
        return None
    said = " ".join(words)
    if said in _NO or words[0] in ("ні", "не", "no", "нет"):
        return False
    if said in _YES or all(w in _YES for w in words):
        return True
    return None


def tokens(text: str) -> list[str]:
    """The words of a phrase, lower-cased: for telling whether two
    requests are about the same thing."""
    return _WORD.findall((text or "").lower())


from heare.language.promises import claims_done, promises_work  # noqa: E402

__all__ += ["claims_done", "promises_work", "tokens"]
