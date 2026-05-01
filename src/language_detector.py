"""Language detection via Claude for multi-language conversation support.

Detects the language of transcribed text using Claude and maps it to TTS voices.
Supports: English (en-US-AriaNeural), Ukrainian (uk-UA-OstapNeural), Russian (ru-RU-DmitryNeural).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("heare.language_detector")

LANG_TO_VOICE = {
    "en": "en-US-AriaNeural",
    "uk": "uk-UA-OstapNeural",
    "ru": "ru-RU-DmitryNeural",
}

LANG_TO_NAME = {
    "en": "English",
    "uk": "Ukrainian",
    "ru": "Russian",
}

SUPPORTED_LANGS = {"en", "uk", "ru"}


async def detect_language_via_claude(
    text: str,
    llm_client: object,
) -> str:
    """Detect language of text using Claude API.

    Args:
        text: Transcribed text to detect language for
        llm_client: AsyncAnthropic or OpenAI-compatible async client with a message() method

    Returns:
        Language code ("en", "uk", or "ru"), or "en" if detection fails
    """
    if not text or not text.strip():
        return "en"

    prompt = f"""Detect the language of the following text. Respond with ONLY the ISO 639-1 language code (en, uk, or ru).

Text: {text[:200]}"""

    try:
        # Handle both AsyncAnthropic and OpenAI clients
        if hasattr(llm_client, "messages"):
            # Anthropic API
            response = await llm_client.messages.create(
                model="claude-3-5-sonnet-20241022",  # use latest available
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}],
            )
            detected = response.content[0].text.strip().lower()
        else:
            # Fallback for other clients
            logger.warning(
                "language_detector: unknown client type, falling back to heuristic detection"
            )
            return _detect_language_heuristic(text)
    except Exception as e:
        logger.warning(
            "language_detector: Claude detection failed, falling back to heuristic: %s", e
        )
        return _detect_language_heuristic(text)

    # Validate and normalize result
    if detected in SUPPORTED_LANGS:
        logger.info("language_detector: detected language=%s from text=%r", detected, text[:40])
        return detected

    logger.warning(
        "language_detector: detected unsupported language %r, falling back to heuristic",
        detected,
    )
    return _detect_language_heuristic(text)


def _detect_language_heuristic(text: str) -> str:
    """Heuristic language detection based on Cyrillic/Latin character distribution.

    Falls back when Claude detection fails or times out.
    """
    if not text:
        return "en"

    text_lower = text.lower()

    # Character set detection
    cyrillic_chars = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    latin_chars = sum(1 for c in text if "a" <= c <= "z" or "A" <= c <= "Z")

    if cyrillic_chars == 0 and latin_chars > 0:
        return "en"

    if cyrillic_chars > 0:
        # Heuristic: Ukrainian uses ґ, є, ї, і; Russian uses ы, э
        uk_markers = ("ґ", "є", "ї", "і")
        ru_markers = ("ы", "э", "ё")

        uk_count = sum(text.count(m) for m in uk_markers)
        ru_count = sum(text.count(m) for m in ru_markers)

        if uk_count > ru_count:
            return "uk"
        elif ru_count > uk_count:
            return "ru"

        # Tie-breaker: word patterns
        if " не " in text_lower or " то " in text_lower:  # Common Ukrainian/Russian words
            return "uk" if " ж " in text_lower or " а то " in text_lower else "ru"

    return "en"


def get_voice_for_language(lang: str) -> str:
    """Get TTS voice name for detected language.

    Args:
        lang: Language code ("en", "uk", "ru")

    Returns:
        Voice name for edge-tts (e.g., "en-US-AriaNeural")
    """
    return LANG_TO_VOICE.get(lang, "en-US-AriaNeural")


def get_language_name(lang: str) -> str:
    """Get human-readable language name.

    Args:
        lang: Language code ("en", "uk", "ru")

    Returns:
        Language name (e.g., "English")
    """
    return LANG_TO_NAME.get(lang, "English")
