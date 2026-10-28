"""English in the voice: wake, turn, recogniser junk, and the spoken voice.

Everything here is offline: phrase tables and pure functions, plus the
channel's voice choice with the synthesiser replaced by a recorder. Nothing
touches a microphone, a recogniser or the network.
"""

from __future__ import annotations

from types import SimpleNamespace

from heare.channels.voice import channel
from heare.channels.voice.channel import Voice
from heare.channels.voice.hallucinations import is_junk
from heare.channels.voice.language import detect_language_from_text, language_of
from heare.channels.voice.turn import sounds_unfinished
from heare.channels.voice.voicing import VOICES, pick_voice, voice_for_language
from heare.channels.voice.wake_phrases import wake_phrases

PERSONA = "You are Doka 🎧 — A capable ambient AI that lives in your headphones."


def test_english_addresses_wake_the_assistant() -> None:
    phrases = wake_phrases(SimpleNamespace(wake_word="гава"), PERSONA)
    for phrase in ("hey heare", "hi heare", "okay heare", "heare"):
        assert phrase in phrases, phrase


def test_a_latin_name_is_heard_in_both_scripts() -> None:
    phrases = wake_phrases(SimpleNamespace(wake_word=""), name="Doka")
    assert "doka" in phrases
    assert "дока" in phrases
    assert "докер" in phrases


def test_a_cyrillic_name_is_also_heard_in_latin() -> None:
    phrases = wake_phrases(SimpleNamespace(wake_word=""), name="Дока")
    assert "дока" in phrases
    assert "доко" in phrases  # Ukrainian vocative kept
    assert "doka" in phrases  # English recogniser writes it in Latin


def test_ukrainian_wake_word_is_still_listened_for() -> None:
    phrases = wake_phrases(SimpleNamespace(wake_word="гава"), PERSONA)
    assert "гава" in phrases


def test_english_words_that_do_not_end_a_turn() -> None:
    for text in (
        "I was going to the shop and",
        "It is fine but",
        "Well,",
        "So",
        "I mean",
        "I mean.",
        "Let me think.",
        "you know",
    ):
        assert sounds_unfinished(text) is True, text


def test_english_finished_sentences_end_the_turn() -> None:
    for text in (
        "What is the weather?",
        "Open the door.",
        "Stop!",
        "Thank you.",
        "I mean it.",
    ):
        assert sounds_unfinished(text) is False, text


def test_ukrainian_continuers_and_finishes_are_unchanged() -> None:
    assert sounds_unfinished("я думаю що") is True
    assert sounds_unfinished("ну типу") is True
    assert sounds_unfinished("Зроби це.") is False
    assert sounds_unfinished("Прийду потім.") is False


def test_english_hallucinations_are_dropped() -> None:
    for text in (
        "Thank you for watching!",
        "Thanks for watching.",
        "Please subscribe.",
        "Like and subscribe",
        "Subtitles by the Amara.org community",
        "Silence",
        "Background music",
        "(applause)",
    ):
        assert is_junk(text) is True, text


def test_english_courtesy_is_dropped_only_into_silence() -> None:
    assert is_junk("Thank you.") is True
    assert is_junk("Thank you.", agent_spoke_recently=True) is False
    assert is_junk("Bye.") is True
    assert is_junk("Bye.", agent_spoke_recently=True) is False


def test_english_speech_survives_the_filter() -> None:
    for text in (
        "What is the weather today?",
        "Open the door, please.",
        "How are you?",
    ):
        assert is_junk(text) is False, text


def test_ukrainian_hallucinations_are_still_dropped() -> None:
    for text in (
        "Дякую за перегляд!",
        "Музика",
        "*тиша*",
        "Субтитри створено спільнотою",
    ):
        assert is_junk(text) is True, text


def test_english_and_ukrainian_are_detected_by_script() -> None:
    assert detect_language_from_text("Hello there", fallback="uk") == "en"
    assert detect_language_from_text("Привіт", fallback="en") == "uk"


def test_language_of_follows_the_setting_and_keeps_the_default() -> None:
    assert language_of(SimpleNamespace(language="en")) == "en"
    assert language_of(SimpleNamespace(language="uk")) == "uk"
    assert language_of(SimpleNamespace()) == "uk"
    assert language_of(SimpleNamespace(groq_language="en", language="uk")) == "en"


def test_the_voice_map_has_english_and_keeps_ukrainian() -> None:
    assert VOICES["en"] == "en-US-AriaNeural"
    assert VOICES["uk"] == "uk-UA-OstapNeural"
    assert VOICES["ru"] == "ru-RU-DmitryNeural"


def test_voice_for_language_keeps_the_ukrainian_default() -> None:
    assert voice_for_language("en") == VOICES["en"]
    assert voice_for_language("uk") == VOICES["uk"]
    assert voice_for_language("") == VOICES["uk"]
    assert voice_for_language("xx") == VOICES["uk"]


def test_pick_voice_still_reads_the_text_script() -> None:
    assert pick_voice("Привіт") == VOICES["uk"]
    assert pick_voice("Hello") == VOICES["en"]
    assert pick_voice("") == VOICES["uk"]


async def test_the_channel_speaks_in_the_chosen_language(monkeypatch) -> None:
    spoken: list[str] = []

    async def fake_synthesise(text, *, voice):
        spoken.append(voice)
        yield b"\x00\x00"

    monkeypatch.setattr(channel, "synthesise", fake_synthesise)
    for language, expected in (("en", VOICES["en"]), ("uk", VOICES["uk"])):
        voice = Voice(object(), SimpleNamespace(language=language, voice=""))
        chunks = [chunk async for chunk in voice._synthesise("Hello")]
        assert chunks and spoken[-1] == expected
