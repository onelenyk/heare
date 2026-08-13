"""TTS voice selection for the spine, per reply text's script.

Edge TTS renders Cyrillic text on an English voice as SILENCE (NoAudioReceived).
This module picks the voice from the reply text's script (Cyrillic → uk/ru,
Latin → en) to avoid that trap.
"""

from src.voice.language.core import LANG_TO_VOICE, detect_language_from_text

VOICES: dict[str, str] = LANG_TO_VOICE
"""Edge TTS voice names by language code (uk/ru/en).
Sourced from src.voice.language.core.LANG_TO_VOICE for consistency."""


def pick_voice(text: str, *, fallback_lang: str = "uk") -> str:
    """Edge TTS voice for this text: Cyrillic -> the uk (or ru, per
    fallback_lang) voice; Latin -> the en voice; empty/undecidable ->
    the fallback language's voice. Returns a full Edge voice name like
    'uk-UA-PolinaNeural'.

    :param text: The text to be spoken.
    :param fallback_lang: Language code for undecidable text (default 'uk').
    :return: Full Edge TTS voice name, e.g. 'uk-UA-OstapNeural'.
    """
    lang = detect_language_from_text(text, fallback=fallback_lang)
    return VOICES.get(lang, VOICES["en"])
