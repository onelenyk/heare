"""Backchannel and interruption decision logic, tested against real phrases.

Backchannels are short acknowledgements that keep the assistant speaking:
Ukrainian «ага», English «yeah», Russian «да», etc. Stop words are explicit
halts: «стоп», «stop», «хватит». Everything else is an interruption.

The decision logic is two-stage: if the user has been speaking long enough
(INTERRUPT_HOLD_MS), interrupt at once. Otherwise, analyse the transcript.
"""
from __future__ import annotations

import pytest

from heare.language.uk import INTERRUPT_HOLD_MS, decide, is_backchannel, is_stop_word


# ============================================================================
# is_backchannel tests: 25+ phrases, both classes
# ============================================================================


@pytest.mark.parametrize(
    "text",
    [
        # Ukrainian acknowledgements.
        "ага",
        "Ага!",
        "ага?",
        "АГА",
        "Ага, ага",
        "ага, ага",
        "угу",
        "Угу?",
        "так",
        "Так!",
        "так-так",
        "так, так",
        "ок",
        "Ок!",
        "окей",
        "Окей?",
        "добре",
        "Добре.",
        "ясно",
        "Ясно!",
        "зрозуміло",
        "Зрозуміло?",
        "мм",
        "ммм",
        # English acknowledgements.
        "yeah",
        "Yeah!",
        "uh-huh",
        "uh huh",
        "ok",
        "okay",
        "mhm",
        "right",
        "Right!",
        # Russian acknowledgements.
        "да",
        "понятно",
    ],
)
def test_backchannel_phrases_are_recognised(text: str) -> None:
    """Short acknowledgements that mean 'go on' are True."""
    assert is_backchannel(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # Negations.
        "ні",
        "Ні!",
        "нет",
        "no",
        "No!",
        # Commands and questions.
        "стоп",
        "далі",
        "тихіше",
        "що?",
        "Що?",
        "а скільки?",
        "не треба",
        "не робити",
        # Not just words but context requires knowing they are commands.
        "замовкни",
        "зупинись",
        # Too long (> 3 words).
        "ага ага ага ага",
        "так так так",
        # Empty or whitespace.
        "",
        "   ",
        "***",
        "[...]",
    ],
)
def test_backchannel_rejects_non_acknowledgements(text: str) -> None:
    """Negations, commands, long phrases, and empty text are False."""
    assert is_backchannel(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "ага ага ага",  # More than 3 words.
        "так так так так",
        "ога ога ога ога",
    ],
)
def test_backchannel_rejects_long_phrases(text: str) -> None:
    """Phrases longer than 3 words are not backchannels."""
    assert is_backchannel(text) is False


# ============================================================================
# is_stop_word tests: 8+ phrases
# ============================================================================


@pytest.mark.parametrize(
    "text",
    [
        # Ukrainian halt commands.
        "стоп",
        "Стоп!",
        "стій",
        "Стій!",
        "зупинись",
        "замовкни",
        "досить",
        "Досить!",
        "тихо",
        "Тихо!",
        # Negations.
        "ні",
        "Ні!",
        "нет",
        "Нет!",
        "no",
        "No!",
        # English.
        "stop",
        "Stop!",
        # Russian.
        "хватит",
        "Хватит!",
    ],
)
def test_stop_words_are_recognised(text: str) -> None:
    """Stop words (explicit halts and negations) are True."""
    assert is_stop_word(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # Acknowledgements are not stop words.
        "ага",
        "угу",
        "так",
        # Empty or whitespace.
        "",
        "   ",
        # Other words.
        "привіт",
        "розкажи",
        "будь ласка",
    ],
)
def test_stop_words_rejects_non_halts(text: str) -> None:
    """Acknowledgements and other words are False."""
    assert is_stop_word(text) is False


# ============================================================================
# decide tests: table covering all paths and edge cases
# ============================================================================


@pytest.mark.parametrize(
    "text,speech_ms,expected",
    [
        # Duration >= INTERRUPT_HOLD_MS → interrupt, regardless of text.
        ("ага", INTERRUPT_HOLD_MS, "interrupt"),
        ("ага", INTERRUPT_HOLD_MS + 100, "interrupt"),
        ("ни", INTERRUPT_HOLD_MS, "interrupt"),
        ("стоп", INTERRUPT_HOLD_MS, "interrupt"),
        ("", INTERRUPT_HOLD_MS, "interrupt"),
        # Duration < INTERRUPT_HOLD_MS: check the transcript.
        # Backchannel → resume.
        ("ага", 0, "resume"),
        ("ага", 100, "resume"),
        ("ага", INTERRUPT_HOLD_MS - 100, "resume"),
        ("угу", 0, "resume"),
        ("так", 0, "resume"),
        ("ок", 0, "resume"),
        ("yeah", 0, "resume"),
        ("да", 0, "resume"),
        ("ага, ага", 0, "resume"),
        # Stop word → cancel.
        ("стоп", 0, "cancel"),
        ("стоп", 100, "cancel"),
        ("ні", 0, "cancel"),
        ("нет", 0, "cancel"),
        ("stop", 0, "cancel"),
        ("хватит", 0, "cancel"),
        ("замовкни", 0, "cancel"),
        # Empty or whitespace → resume (nothing was said).
        ("", 0, "resume"),
        ("", 100, "resume"),
        ("   ", 0, "resume"),
        ("\t\n", 0, "resume"),
        # Anything else → interrupt.
        ("привіт", 0, "interrupt"),
        ("розкажи мне историю", 0, "interrupt"),
        ("скільки вільного місця?", 0, "interrupt"),
        ("будь ласка", 0, "interrupt"),
        # Boundary: just under INTERRUPT_HOLD_MS.
        ("ага", INTERRUPT_HOLD_MS - 1, "resume"),
        # Boundary: just at INTERRUPT_HOLD_MS.
        ("угу", INTERRUPT_HOLD_MS - 1, "resume"),
        ("угу", INTERRUPT_HOLD_MS, "interrupt"),
        # Mixed punctuation and spaces should be normalised.
        ("  Ага!  ", 0, "resume"),
        ("... стоп ...", 0, "cancel"),
        ("   \t  ", 100, "resume"),
    ],
)
def test_decide_table(text: str, speech_ms: float, expected: str) -> None:
    """Comprehensive decision logic test.

    Covers the three main paths: duration threshold (≥600 ms is interrupt),
    backchannel (resume), stop word (cancel), empty/whitespace (resume),
    and everything else (interrupt). Includes boundary conditions at the
    600 ms threshold and edge cases with punctuation and whitespace.
    """
    assert decide(text, speech_ms=speech_ms) == expected


def test_interrupt_hold_ms_is_600() -> None:
    """The hold time is a known constant (a measurement placeholder)."""
    assert INTERRUPT_HOLD_MS == 600
