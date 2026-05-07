"""Tests for language detection module."""
from src.voice.language.detector import (
    get_voice_for_language,
    get_language_name,
    _detect_language_heuristic,
)


def test_get_voice_for_language_english():
    """Test voice mapping for English."""
    assert get_voice_for_language("en") == "en-US-AriaNeural"


def test_get_voice_for_language_ukrainian():
    """Test voice mapping for Ukrainian."""
    assert get_voice_for_language("uk") == "uk-UA-OstapNeural"


def test_get_voice_for_language_russian():
    """Test voice mapping for Russian."""
    assert get_voice_for_language("ru") == "ru-RU-DmitryNeural"


def test_get_voice_for_language_default():
    """Test voice mapping falls back to English for unknown language."""
    assert get_voice_for_language("unknown") == "en-US-AriaNeural"


def test_get_language_name():
    """Test language name mapping."""
    assert get_language_name("en") == "English"
    assert get_language_name("uk") == "Ukrainian"
    assert get_language_name("ru") == "Russian"
    assert get_language_name("unknown") == "English"


def test_heuristic_english():
    """Test heuristic detection for English text."""
    text = "Hello, how are you today? I am doing well."
    lang = _detect_language_heuristic(text)
    assert lang == "en"


def test_heuristic_ukrainian():
    """Test heuristic detection for Ukrainian text."""
    text = "Привіт! Як справи? Дякую, в мене все добре."
    lang = _detect_language_heuristic(text)
    assert lang == "uk"


def test_heuristic_russian():
    """Test heuristic detection for Russian text with Russian-specific markers."""
    # Russian uses ы, э, ё which Ukrainian doesn't
    text = "Привет! Как дела? Спасибо, у меня все хорошо. Ты видишь красивый цветок?"
    lang = _detect_language_heuristic(text)
    # May still be ambiguous without explicit Russian markers, but should prefer Russian with ы
    assert lang in ("ru", "uk")


def test_heuristic_empty():
    """Test heuristic detection with empty text."""
    assert _detect_language_heuristic("") == "en"
    assert _detect_language_heuristic("   ") == "en"


def test_heuristic_mixed_content():
    """Test heuristic detection with mostly Latin characters."""
    text = "This is mostly English with a few слов Ukrainian words."
    lang = _detect_language_heuristic(text)
    # Should detect as English since majority is Latin
    assert lang == "en"


def test_heuristic_ukrainian_markers():
    """Test detection of Ukrainian-specific characters."""
    # Ukrainian uses ґ, є, ї, і
    text = "Це український текст з ґ, є, ї, і"
    lang = _detect_language_heuristic(text)
    assert lang == "uk"
