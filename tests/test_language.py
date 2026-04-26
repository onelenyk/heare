from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Settings, load_settings
from src.language import (
    detect_language_from_frame,
    detect_script_language,
    is_standalone_cancel_imperative,
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


# (n) detect_script_language tests
def test_detect_script_language_cyrillic():
    assert detect_script_language("Привіт світ") == "cyrillic"


def test_detect_script_language_latin():
    assert detect_script_language("Hello world") == "latin"


def test_detect_script_language_empty():
    assert detect_script_language("") == "unknown"


# ============================================================================
# US-WU-02: is_standalone_cancel_imperative — promoted from src/decider.py.
# Fixture-driven parametrized tests against tests/fixtures/cancel_stopwords.txt.
# Migrated from tests/test_decider.py.
# ============================================================================


def _load_cancel_fixtures() -> tuple[list[str], list[str]]:
    """Load TRIGGER + NO-TRIGGER utterances from tests/fixtures/cancel_stopwords.txt."""
    fixture_path = Path(__file__).parent / "fixtures" / "cancel_stopwords.txt"
    triggers: list[str] = []
    no_triggers: list[str] = []
    for line in fixture_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        verdict, utt = parts[0].strip(), parts[1].strip()
        if verdict == "TRIGGER":
            triggers.append(utt)
        elif verdict == "NO-TRIGGER":
            no_triggers.append(utt)
    return triggers, no_triggers


_TRIGGERS, _NO_TRIGGERS = _load_cancel_fixtures()


@pytest.mark.parametrize("utterance", _TRIGGERS)
def test_is_standalone_cancel_imperative_fixtures_trigger(utterance: str) -> None:
    """Stop-word fixtures (en/uk/ru) trigger the standalone-imperative detector."""
    settings = Settings()
    assert is_standalone_cancel_imperative(utterance, settings.cancel_stop_words), (
        f"expected TRIGGER for {utterance!r}"
    )


@pytest.mark.parametrize("utterance", _NO_TRIGGERS)
def test_is_standalone_cancel_imperative_fixtures_no_trigger(utterance: str) -> None:
    """Negations and noun-context utterances do NOT trigger."""
    settings = Settings()
    assert not is_standalone_cancel_imperative(
        utterance, settings.cancel_stop_words
    ), f"expected NO-TRIGGER for {utterance!r}"


def test_is_standalone_cancel_imperative_empty_input() -> None:
    """Empty / whitespace-only input never triggers."""
    settings = Settings()
    assert not is_standalone_cancel_imperative("", settings.cancel_stop_words)
    assert not is_standalone_cancel_imperative("   ", settings.cancel_stop_words)


def test_is_standalone_cancel_imperative_empty_stop_words() -> None:
    """Empty stop-word list never triggers (defensive — caller has nothing to match)."""
    assert not is_standalone_cancel_imperative("stop", [])


def test_cancel_stop_words_extensible_via_env(monkeypatch) -> None:
    """HEARE_CANCEL_STOP_WORDS env var REPLACES the default stop-word list."""
    monkeypatch.setenv("HEARE_CANCEL_STOP_WORDS", "foo,bar")
    new_settings = load_settings()
    assert "foo" in new_settings.cancel_stop_words
    assert "bar" in new_settings.cancel_stop_words

    # Standalone "foo" / "please bar" must now trigger.
    assert is_standalone_cancel_imperative("foo", new_settings.cancel_stop_words)
    assert is_standalone_cancel_imperative(
        "please bar", new_settings.cancel_stop_words
    )
    # And a non-listed legacy stop-word must NOT trigger under the override
    # (semantics: env var REPLACES the list; this guards the override is
    # actually exclusive, not additive).
    assert not is_standalone_cancel_imperative(
        "stop", new_settings.cancel_stop_words
    )
