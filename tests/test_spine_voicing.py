"""Tests for src.spine.voicing — TTS voice selection per text script."""

import re
from src.spine.voicing import VOICES, pick_voice


def test_ukrainian_text_returns_uk_voice() -> None:
    """Test 1: Ukrainian text -> uk voice."""
    text = "Привіт, як дела?"
    voice = pick_voice(text)
    assert voice == VOICES["uk"]
    assert voice.startswith("uk-")


def test_english_text_returns_en_voice() -> None:
    """Test 2: English text -> en voice."""
    text = "Hello, how are you?"
    voice = pick_voice(text)
    assert voice == VOICES["en"]
    assert voice.startswith("en-")


def test_mixed_text_cyrillic_wins() -> None:
    """Test 3: Mixed text with Cyrillic present -> Cyrillic wins."""
    text = "Hello привіт world"
    voice = pick_voice(text)
    # Cyrillic is present, so should use Cyrillic voice
    assert voice == VOICES["uk"], f"Expected {VOICES['uk']}, got {voice}"


def test_empty_string_returns_fallback() -> None:
    """Test 4: Empty string -> fallback voice (default uk)."""
    voice = pick_voice("")
    assert voice == VOICES["uk"]


def test_fallback_ru_with_cyrillic() -> None:
    """Test 5: fallback_lang='ru' + Cyrillic -> ru voice."""
    text = "Привет, как дела?"
    voice = pick_voice(text, fallback_lang="ru")
    assert voice == VOICES["ru"]
    assert voice.startswith("ru-")


def test_numbers_punctuation_only_returns_fallback() -> None:
    """Test 6: Numbers/punctuation only -> fallback voice."""
    text = "123 !!! ... ???"
    voice = pick_voice(text)
    assert voice == VOICES["uk"]  # Default fallback


def test_numbers_punctuation_with_ru_fallback() -> None:
    """Numbers/punctuation with ru fallback."""
    text = "456 *** ;;; :::"
    voice = pick_voice(text, fallback_lang="ru")
    assert voice == VOICES["ru"]


def test_all_voices_valid_format() -> None:
    r"""Test 7: Every value in VOICES looks like a valid Edge voice name.

    Valid format: ^[a-z]{2}-[A-Z]{2}-\w+Neural$
    Examples: 'uk-UA-OstapNeural', 'en-US-AriaNeural', 'ru-RU-DmitryNeural'
    """
    pattern = re.compile(r"^[a-z]{2}-[A-Z]{2}-\w+Neural$")
    for lang_code, voice_name in VOICES.items():
        assert pattern.match(voice_name), (
            f"Voice for {lang_code} ({voice_name!r}) does not match "
            f"pattern ^[a-z]{{2}}-[A-Z]{{2}}-\\w+Neural$"
        )


def test_explicit_fallback_en() -> None:
    """Fallback to English when no Cyrillic/Latin detected."""
    text = "123"
    voice = pick_voice(text, fallback_lang="en")
    assert voice == VOICES["en"]


def test_russian_cyrillic_text() -> None:
    """Russian Cyrillic text with default fallback -> uk voice."""
    text = "Здравствуйте, как ваши дела?"
    voice = pick_voice(text)
    # Russian Cyrillic should resolve to uk by default (fallback_lang="uk")
    assert voice == VOICES["uk"]


def test_russian_cyrillic_text_ru_fallback() -> None:
    """Russian Cyrillic text with ru fallback -> ru voice."""
    text = "Здравствуйте, как ваши дела?"
    voice = pick_voice(text, fallback_lang="ru")
    assert voice == VOICES["ru"]


def test_whitespace_only_returns_fallback() -> None:
    """Whitespace-only string -> fallback voice."""
    voice = pick_voice("   \t\n  ")
    assert voice == VOICES["uk"]
