"""Being addressed.

An always-on assistant that answers everything answers the room.
Observed live: a podcast nearby produced a turn every few seconds, each
starting a model call that the next sentence interrupted, so it never
finished a reply and never stopped trying.
"""

from __future__ import annotations

from src.config import Settings
from src.pipeline.wake import _name_from_persona, wake_phrases

PERSONA = "You are Doka 🎧 — A capable ambient AI that lives in your headphones."


def test_the_name_comes_from_the_generated_identity() -> None:
    """It is generated at first run, so nobody can hard-code it."""
    assert _name_from_persona(PERSONA) == "Doka"
    assert _name_from_persona("You are Kort, an assistant.") == "Kort"
    assert _name_from_persona("") == ""


def test_phrases_cover_what_speech_recognition_actually_produces() -> None:
    """Matching is exact-word over the transcript, so the list has to hold
    what Whisper writes, not how the name is spelled. In one session it
    produced "докер", "дока" and "Дока" for the same word."""
    phrases = wake_phrases(Settings(), PERSONA)

    for heard in ("doka", "дока", "докер"):
        assert heard in phrases


def test_both_the_name_and_the_wake_word_are_listened_for() -> None:
    """People call it by name; the wake word is a separate setting that
    may never have been changed from its default."""
    settings = Settings()
    settings.wake_word = "гава"
    phrases = wake_phrases(settings, PERSONA)

    assert "doka" in phrases
    assert "гава" in phrases


def test_the_list_is_never_empty() -> None:
    """An empty phrase list would gate on nothing and block every turn —
    the assistant would go permanently deaf."""
    settings = Settings()
    settings.wake_word = ""
    assert wake_phrases(settings, "") == ["гава"]


def test_no_duplicates() -> None:
    settings = Settings()
    settings.wake_word = "doka"
    phrases = wake_phrases(settings, PERSONA)
    assert len(phrases) == len(set(phrases))


# ── the settings ──────────────────────────────────────────────────────


def test_addressing_is_required_by_default() -> None:
    assert Settings().wake_required is True


def test_a_follow_up_needs_no_name() -> None:
    """"Гава, скільки місця на диску" — "а на другому?" is one
    conversation, not two summonses."""
    settings = Settings()
    assert settings.wake_every_turn is False
    assert settings.wake_window_seconds >= 20, (
        "a window shorter than a reply plus a thought makes it useless"
    )
