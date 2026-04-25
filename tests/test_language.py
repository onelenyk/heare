from types import SimpleNamespace

from src.language import (
    check_cancel,
    detect_language_from_frame,
    detect_script_language,
    voice_for_language,
)


# (a) voice_for_language returns correct voice for en/uk/ru
def test_voice_for_language_en():
    assert voice_for_language("en") == "en-US-AriaNeural"


def test_voice_for_language_uk():
    assert voice_for_language("uk") == "uk-UA-OstapNeural"


def test_voice_for_language_ru():
    assert voice_for_language("ru") == "ru-RU-DmitryNeural"


# (b) voice_for_language returns English voice for unknown lang "fr"
def test_voice_for_language_unknown():
    assert voice_for_language("fr") == "en-US-AriaNeural"


# (c) detect_language_from_frame extracts "uk" from mock frame with result.language = "ukrainian"
def test_detect_language_from_frame_ukrainian():
    frame = SimpleNamespace(result=SimpleNamespace(language="ukrainian"))
    assert detect_language_from_frame(frame) == "uk"


# (d) detect_language_from_frame extracts "en" from mock frame with result.language = "english"
def test_detect_language_from_frame_english():
    frame = SimpleNamespace(result=SimpleNamespace(language="english"))
    assert detect_language_from_frame(frame) == "en"


# (e) detect_language_from_frame returns fallback when frame has no result attribute
def test_detect_language_from_frame_no_result():
    frame = SimpleNamespace()
    assert detect_language_from_frame(frame, fallback="en") == "en"


# (f) detect_language_from_frame returns fallback when result.language is unsupported
def test_detect_language_from_frame_unsupported():
    frame = SimpleNamespace(result=SimpleNamespace(language="japanese"))
    assert detect_language_from_frame(frame, fallback="en") == "en"


# (g) detect_language_from_frame returns fallback when result.language is None
def test_detect_language_from_frame_none_language():
    frame = SimpleNamespace(result=SimpleNamespace(language=None))
    assert detect_language_from_frame(frame, fallback="en") == "en"


# (h) check_cancel("cancel this", "en") returns True
def test_check_cancel_english_cancel():
    assert check_cancel("cancel this", "en") is True


# (i) check_cancel("скасуй", "uk") returns True
def test_check_cancel_ukrainian():
    assert check_cancel("скасуй", "uk") is True


# (j) check_cancel("отмени", "ru") returns True
def test_check_cancel_russian():
    assert check_cancel("отмени", "ru") is True


# (k) check_cancel("hello", "en") returns False
def test_check_cancel_no_match():
    assert check_cancel("hello", "en") is False


# (l) check_cancel("cancel", "uk") returns True (English fallback)
def test_check_cancel_english_fallback_for_uk():
    assert check_cancel("cancel", "uk") is True


# (m) check_cancel("стоп", "uk") returns True (in uk patterns)
def test_check_cancel_stop_ukrainian():
    assert check_cancel("стоп", "uk") is True


# (n) detect_script_language tests
def test_detect_script_language_cyrillic():
    assert detect_script_language("Привіт світ") == "cyrillic"


def test_detect_script_language_latin():
    assert detect_script_language("Hello world") == "latin"


def test_detect_script_language_empty():
    assert detect_script_language("") == "unknown"
