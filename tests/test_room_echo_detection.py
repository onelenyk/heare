"""Telling the assistant's own voice from the person's.

This is the one thing every scenario checks, and for a while it checked
nothing: a run where the assistant answered its own voice eight times
scored zero self-hearing and passed. The detector asked whether the
transcript matched the current speech fragment while the bot was still
speaking — but recognition lands a second or two late, by which time the
bot has stopped, and a fragment is only a few words long.
"""

from __future__ import annotations

from src.pipeline.room import _best_overlap, _is_echo, _overlap


def test_a_transcript_of_the_persons_own_line_is_not_echo() -> None:
    person = ["Дока, перелічи планети не поспішаючи"]
    bot = ["Перша планета — Меркурій, найближча до Сонця"]

    assert _is_echo("Дока, перелічи планети", person, bot) is False


def test_the_assistants_sentence_coming_back_is_echo() -> None:
    """Observed live: "Сатурн, відомий своїми кільцями" returned through
    the microphone, passed the gate, and was answered as a question."""
    person = ["Дока, перелічи планети не поспішаючи", "Стоп, зачекай."]
    bot = ["Шоста планета — Сатурн, відомий своїми кільцями"]

    assert _is_echo("Сатурн, відомий своїми кільцями.", person, bot) is True


def test_echo_is_caught_after_the_assistant_has_stopped_speaking() -> None:
    """No "is it speaking right now" condition: that was the bug. The
    words arrive late by definition."""
    person = ["привіт"]
    bot = ["Восьма і остання — Нептун"]

    assert _is_echo("Восьма і остання. Нептун.", person, bot) is True


def test_a_word_shared_with_the_assistant_is_not_enough() -> None:
    """Both sides say "планети"; only one of them is an echo."""
    person = ["а скільки всього планет у системі"]
    bot = ["У Сонячній системі вісім планет"]

    assert _is_echo("а скільки всього планет у системі", person, bot) is False


def test_silence_is_not_echo() -> None:
    assert _is_echo("", ["привіт"], ["вітаю"]) is False
    assert _is_echo("   ", ["привіт"], ["вітаю"]) is False


def test_nothing_said_yet_cannot_be_echoed() -> None:
    assert _is_echo("привіт", [], []) is False


# ── the pieces ────────────────────────────────────────────────────────


def test_lines_are_compared_one_at_a_time() -> None:
    """Joined into one string, a long history shares a word with
    everything and the score stops distinguishing anything."""
    candidates = ["зовсім про інше", "Сатурн відомий своїми кільцями"]

    assert _best_overlap("Сатурн відомий кільцями", candidates) > 0.5
    assert _best_overlap("нічого спільного", candidates) == 0.0


def test_overlap_is_symmetric_in_length() -> None:
    """A short transcript inside a long sentence still scores high — one
    recognised clause is enough to identify the source."""
    assert _overlap("Сатурн кільцями", "Шоста планета Сатурн відомий своїми кільцями") == 1.0


def test_empty_sides_score_nothing() -> None:
    assert _overlap("", "щось") == 0.0
    assert _overlap("щось", "") == 0.0
    assert _best_overlap("щось", []) == 0.0
