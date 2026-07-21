

# ---------------------------------------------------------------------------
# detect_language_from_text — plain text has no Whisper result to read, and
# the wrong voice means Edge TTS returns no audio at all.
# ---------------------------------------------------------------------------


def test_detect_language_from_text_cyrillic_uses_fallback() -> None:
    from src.voice.language.core import detect_language_from_text

    assert detect_language_from_text("Скільки буде два плюс два?", fallback="uk") == "uk"
    assert detect_language_from_text("Сколько будет два плюс два?", fallback="ru") == "ru"


def test_detect_language_from_text_cyrillic_defaults_to_uk() -> None:
    """A non-Cyrillic fallback must not send Cyrillic to an English voice."""
    from src.voice.language.core import detect_language_from_text

    assert detect_language_from_text("Чотири.", fallback="en") == "uk"


def test_detect_language_from_text_latin_is_english() -> None:
    from src.voice.language.core import detect_language_from_text

    assert detect_language_from_text("How much is two plus two?", fallback="uk") == "en"


def test_detect_language_from_text_mixed_prefers_cyrillic() -> None:
    """The greeting 'Doka на зв'язку' is mixed; it must not read as English."""
    from src.voice.language.core import detect_language_from_text

    assert detect_language_from_text("Doka на зв'язку", fallback="uk") == "uk"


def test_detect_language_from_text_empty_and_neutral() -> None:
    from src.voice.language.core import detect_language_from_text

    assert detect_language_from_text("", fallback="uk") == "uk"
    assert detect_language_from_text("123 456", fallback="uk") == "uk"
