import re

SUPPORTED_LANGS: set[str] = {"en", "uk", "ru"}
DEFAULT_LANG: str = "en"
LANG_NAMES: dict[str, str] = {"en": "English", "uk": "Ukrainian", "ru": "Russian"}
LANG_TO_VOICE: dict[str, str] = {
    "en": "en-US-AriaNeural",
    "uk": "uk-UA-OstapNeural",
    "ru": "ru-RU-DmitryNeural",
}
WHISPER_NAME_TO_ISO: dict[str, str] = {
    "english": "en",
    "ukrainian": "uk",
    "russian": "ru",
}


# CCS-05a: politeness markers that may precede a stop-word in a standalone
# cancel imperative (e.g. "please cancel", "будь ласка стоп"). Stripped
# at the START of the utterance only — they cannot appear mid-utterance.
_CANCEL_POLITENESS_PREFIXES: tuple[str, ...] = (
    "будь ласка",  # uk (multi-word — strip first)
    "пожалуйста",  # ru
    "please",
    "ok",
    "okay",
)
# Filler tokens stripped from the start of the utterance before the
# stop-word detector evaluates the remainder.
_CANCEL_FILLER_PREFIXES: tuple[str, ...] = ("hey", "yo", "uh", "um")
# Negation tokens — when ANY of these appear in the original (lowercased,
# punctuation-stripped) utterance, the utterance is NOT a cancel
# imperative. Order matters: longer phrases ("do not", "will not") first.
_CANCEL_NEGATION_TOKENS: tuple[str, ...] = (
    "do not",
    "will not",
    "don't",
    "won't",
    "не",  # uk/ru
)
# Context tokens that disambiguate stop/cancel as a NOUN rather than an
# imperative ("stop sign", "stop is a four-letter word", "the cancel
# button"). Their presence anywhere in the residual utterance kills the
# trigger.
_CANCEL_CONTEXT_TOKENS: tuple[str, ...] = (
    "is",
    "isn't",
    "are",
    "aren't",
    "was",
    "were",
    "sign",
    "word",
    "button",
)
# Max words in the residual utterance (after politeness/filler strip).
# Longer utterances are conversation, not commands.
_CANCEL_MAX_RESIDUAL_WORDS: int = 4


def detect_language_from_frame(frame, fallback: str = "en") -> str:
    try:
        result = getattr(frame, "result", None)
        if result is None:
            return fallback
        lang_name = getattr(result, "language", None)
        if lang_name is None:
            return fallback
        iso = WHISPER_NAME_TO_ISO.get(lang_name.lower())
        if iso and iso in SUPPORTED_LANGS:
            return iso
        return fallback
    except Exception:
        return fallback


def voice_for_language(lang: str) -> str:
    return LANG_TO_VOICE.get(lang, LANG_TO_VOICE["en"])


def is_standalone_cancel_imperative(
    text: str, stop_words: list[str]
) -> bool:
    """CCS-05a: detect a STANDALONE cancel imperative.

    Trigger criteria (ALL must hold):
      1. After lowercase + punctuation-normalisation, the utterance
         contains NO negation tokens (``don't``, ``do not``, ``won't``,
         ``will not``, ``не``). Negation anywhere in the utterance
         disqualifies it. This precludes ``don't stop``, ``I won't
         stop``, ``не зупиняйся``.
      2. After stripping (in order) the longest matching politeness
         prefix and any leading filler tokens, the residual is ≤4 words.
      3. The first residual word matches one of ``stop_words``
         (case-insensitive).
      4. The residual contains NO context tokens (``is``, ``sign``,
         ``word``, ``button``, …) — these flag the stop-word as a noun
         rather than an imperative.

    Pure / synchronous — safe to call from the generator's frame-processing
    hot path with no event-loop yield.

    Edge case (documented per consensus condition #1):
      ``no no no stop`` — strictly four words. After stripping there are
      no politeness/filler prefixes; the first word ``no`` is NOT a
      stop-word, so by rule (3) this would NOT trigger. The plan asks us
      to TRIGGER on this fixture because the imperative tail "stop"
      dominates. Handled as a small special case: if every residual word
      before the stop-word is the literal token ``no``, we treat the
      utterance as triggering. This keeps the rule otherwise tight.
    """
    if not text:
        return False
    raw = text.strip().lower()
    if not raw:
        return False
    # Normalise punctuation to spaces so tokenisation is uniform.
    cleaned = re.sub(r"[\.,\!\?:;\"'()]+", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
    # Rule 1: negation kill. Use word boundaries — "не" matches " не " not "нерухомо".
    for tok in _CANCEL_NEGATION_TOKENS:
        # Match as a whole token (surrounded by spaces or string edges).
        if re.search(rf"(^|\s){re.escape(tok)}(\s|$)", cleaned):
            return False
    # Strip ONE longest politeness prefix at start.
    residual = cleaned
    for prefix in sorted(_CANCEL_POLITENESS_PREFIXES, key=len, reverse=True):
        if residual == prefix or residual.startswith(prefix + " "):
            residual = residual[len(prefix):].lstrip()
            break
    # Strip leading filler tokens (zero or more, single tokens).
    residual_tokens = residual.split()
    while residual_tokens and residual_tokens[0] in _CANCEL_FILLER_PREFIXES:
        residual_tokens.pop(0)
    if not residual_tokens:
        return False
    # Rule 2: residual must be ≤4 words.
    if len(residual_tokens) > _CANCEL_MAX_RESIDUAL_WORDS:
        return False
    stop_set = {w.lower().strip() for w in stop_words if w and w.strip()}
    if not stop_set:
        return False
    first = residual_tokens[0]
    # "no no no stop" carve-out: residual is e.g. ["no", "no", "no", "stop"].
    # Strictly all-no leading tokens followed by a stop-word counts.
    if first == "no" and len(residual_tokens) <= _CANCEL_MAX_RESIDUAL_WORDS:
        non_no = [t for t in residual_tokens if t != "no"]
        if (
            non_no
            and non_no[0] in stop_set
            and not any(t in _CANCEL_CONTEXT_TOKENS for t in non_no[1:])
        ):
            return True
        # Otherwise fall through — first token "no" is not a stop-word.
    if first not in stop_set:
        return False
    # Rule 4: context tokens disqualify (stop sign, stop is a, the cancel button).
    for tok in residual_tokens[1:]:
        if tok in _CANCEL_CONTEXT_TOKENS:
            return False
    return True


def detect_script_language(text: str) -> str:
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    latin = sum(1 for c in text if ("A" <= c <= "Z") or ("a" <= c <= "z"))
    total = cyrillic + latin
    if total == 0:
        return "unknown"
    if cyrillic / total > 0.5:
        return "cyrillic"
    if latin / total > 0.5:
        return "latin"
    return "unknown"
