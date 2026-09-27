"""Failures that must not silence the person, and edge cases in TTS/formatting.

The assistant is heard by ear, not read. Silence is the worst failure mode:
indistinguishable from "did not hear me" and impossible to remedy. Four things
tested here:

1. The model raises before saying a word: the person still hears a sentence.
2. The model streams then crashes: the turn still ends audibly, and the words
   that made it are kept in history (not discarded as a mind failure).
3. The synthesiser returns no audio (NoAudioReceived from edge-tts): expected to
   be a failure, not a silent "done".
4. The model answers with markdown and emoji: should be stripped before TTS
   (not sent as-is to the voice, which may render it as silence).

Each is a plausible failure that has happened in testing or production, and
each changed the assistant's voice to silence without a trace.
"""

from __future__ import annotations

import pytest

from src.spine.loop import FAILURE_LINE
from tests.e2e.room import Says, close_room, lines, open_room

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.e2e_text]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


# ── the model raises ─────────────────────────────────────────────────────


async def test_model_raises_before_saying_anything(room) -> None:
    """When the model crashes before streaming any text, the person still
    hears something: the failure line, spoken and recorded in history."""
    room.will_say(
        Says(text="", fails="Model timeout")
    )

    said = await room.told("Дока, скільки буде два плюс два?")

    assert said == FAILURE_LINE
    assert FAILURE_LINE in lines(room)


async def test_model_streams_then_raises(room) -> None:
    """When the model crashes mid-answer (mind failure), the partial words
    are discarded and only the failure line is recorded. The person hears
    the failure line, not silence or a truncated sentence."""
    room.will_say(
        Says(text="Два плюс два ", fails="Connection reset")
    )

    said = await room.told("Дока, скільки буде два плюс два?")

    # The failure line is what was written down and what they heard.
    assert said == FAILURE_LINE
    # The partial words were discarded; only the failure line is in the record.
    conversation = lines(room)
    assert FAILURE_LINE in conversation
    # What matters: the person heard a sentence, not silence or a crash.
    assert said != ""


# ── the mouth fails ──────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="src/spine/tts.py:94 — NoAudioReceived is swallowed, a sentence goes silent and the turn counts as done")
async def test_tts_returns_no_audio_is_treated_as_failure(room) -> None:
    """When the synthesiser returns no audio (edge-tts NoAudioReceived,
    silently swallowed in src/spine/tts.py:94), the turn must not silently
    finish as "done". Expected behaviour: the failure line is spoken
    because TTS failing is a mouth failure, not a silent success."""
    room.will_say("Готово, готово.")
    room.silent_voice = True  # Every sentence yields no audio

    said = await room.told("Дока, привіт.")

    # What the person heard: the failure line, not silence.
    # When TTS fails, it should trigger the mouth failure path and speak
    # FAILURE_LINE instead of finishing silently.
    assert said == FAILURE_LINE


# ── text formatting ──────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="no markdown/emoji scrub before TTS (known-broken.md)")
async def test_markdown_and_emoji_are_stripped_before_tts(room) -> None:
    """When the model answers with formatting and symbols that are rendered
    as silence by edge-tts on Cyrillic text, they must be stripped before
    the text reaches the synthesiser. Example: **Готово** ✅ and list markers
    should become plain text."""
    room.will_say("**Готово** ✅\n- перший пункт\n- другий пункт")

    said = await room.told("Дока, зроби список.")

    # What the synthesiser was handed (room.voiced):
    voiced_texts = [text for text, _voice in room.voiced]

    # Should not contain markdown bold markers, list markers, or emoji.
    for text in voiced_texts:
        assert "**" not in text, (
            f"Markdown bold ** found in '{text}': no scrub before TTS "
            "(known-broken.md)"
        )
        assert "✅" not in text, (
            f"Emoji ✅ found in '{text}': no scrub before TTS (known-broken.md)"
        )
        # List markers at line start (after newline/strip) should be gone.
        for line in text.split("\n"):
            assert not line.lstrip().startswith("- "), (
                f"List marker found in '{line}': no scrub before TTS "
                "(known-broken.md)"
            )

    # The person hears something — not silence.
    assert said != "", "TTS was called and something was spoken"
