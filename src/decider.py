"""Residual surface from the pre-S2S DeciderProcessor.

US-WU-05 deleted the dormant ``DeciderProcessor`` class (~900 lines), its
dormant helpers (``is_noise``, ``is_quick_nothing``, ``_redact_passphrase``,
``_keyword_is_adjacent_prefix``, ``_looks_like_question``,
``_is_mostly_non_ukrainian``, plus the wake-word / question / Cyrillic
regexes that only those helpers used) and the ``tests/test_decider.py``
suite. ``DeciderProcessor`` was never instantiated by ``build_pipeline``
once the S2S generator pipeline shipped — production runs
``GeneratorProcessor``.

What remains here:

* ``parse_yes_no`` — pure head-anchored yes/no parser. Still imported by
  ``tests/test_yes_no.py`` and is generically useful for any future
  confirmation flow inside ``GeneratorProcessor``. Cheap to keep.
* ``create_decider_processor`` — stub that raises ``RuntimeError`` on
  call. Six sibling test files (``test_audio.py``, ``test_feature_flags``,
  ``test_mode_hot_reload``, ``test_silent_timeout``,
  ``test_stranger_integration``) still reference this name at import
  time. They are skip-marked pending migration to
  ``GeneratorProcessor`` (PRD WU US-WU-05).
"""
from __future__ import annotations

import re

# Vocative prefix stripped before head-matching so "гава так" → "так".
_VOCATIVES = re.compile(r"^\s*(гава|heare|гей)[\s,]+", re.IGNORECASE)

_YES_HEAD = re.compile(
    r"^(так|да|ага|окей|ok|yes|yeah|sure|go|давай|зроби|вперед|конечно|красава)\b",
    re.IGNORECASE,
)
_NO_HEAD = re.compile(
    r"^(ні|нет|не|nevermind|cancel|stop|skip|no|abort)\b",
    re.IGNORECASE,
)
# "так не роби", "давай не зараз" — YES token immediately followed by "не" inverts to NO.
_YES_THEN_NE = re.compile(
    r"^(так|да|ok|yes|давай|ага|окей)\s+не\b", re.IGNORECASE
)
# "не треба", "не потрібно", "не зараз" — tail negation after YES head → NO.
_NEGATION_TAIL = re.compile(
    r"\bне\s+(треба|потрібно|зараз|роби|хочу|робимо)\b", re.IGNORECASE
)

MAX_YES_NO_WORDS = 4  # utterances longer than this are dialogue, not confirmations


def parse_yes_no(text: str) -> str:
    """Return 'yes', 'no', or 'unclear'.

    Head-anchored: the utterance must START with a yes/no token (after
    optional vocative strip). Beyond MAX_YES_NO_WORDS words → 'unclear'.
    """
    raw = text.strip().lower()
    if not raw:
        return "unclear"
    # Strip leading vocative so "гава так" → "так", "гава, ні" → "ні"
    cleaned = _VOCATIVES.sub("", raw)
    # Normalise punctuation and whitespace
    cleaned = re.sub(r"[\.,\!\?]+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "unclear"
    words = cleaned.split()
    if len(words) > MAX_YES_NO_WORDS:
        return "unclear"
    # Lone "не"
    if cleaned == "не":
        return "no"
    # NO head wins unconditionally
    if _NO_HEAD.match(cleaned):
        return "no"
    # YES head + negation inversion
    if _YES_HEAD.match(cleaned):
        if _YES_THEN_NE.match(cleaned):
            return "no"
        if _NEGATION_TAIL.search(cleaned):
            return "no"
        return "yes"
    return "unclear"


def create_decider_processor(*args, **kwargs):
    """Stub — DeciderProcessor was deleted in US-WU-05.

    Sibling tests still import this name; they are skip-marked pending
    migration to GeneratorProcessor. Calling this stub at runtime is a
    bug — the live pipeline uses ``GeneratorProcessor`` from
    ``src/generator.py``.
    """
    raise RuntimeError(
        "DeciderProcessor removed; sibling tests pending GeneratorProcessor migration"
    )
