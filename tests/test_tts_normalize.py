"""Tests for src/voice/tts/normalize.py — Cyrillic run-on word detection and repair."""
from __future__ import annotations

import pytest

from src.voice.tts.normalize import (
    MIN_CYRILLIC_RUN,
    has_run_on_cyrillic,
    normalize_cyrillic_spacing,
)


class TestHasRunOnCyrillic:
    def test_english_unchanged(self) -> None:
        assert not has_run_on_cyrillic("Hello world, how are you?")

    def test_normal_cyrillic_with_spaces(self) -> None:
        assert not has_run_on_cyrillic("Привіт, як справи?")

    def test_run_on_cyrillic_detected(self) -> None:
        long_run = "це" * (MIN_CYRILLIC_RUN // 2 + 1)
        assert has_run_on_cyrillic(long_run)

    def test_short_cyrillic_run_not_detected(self) -> None:
        short_run = "це" * 5
        assert not has_run_on_cyrillic(short_run)

    def test_empty_text(self) -> None:
        assert not has_run_on_cyrillic("")

    def test_punctuation_only(self) -> None:
        assert not has_run_on_cyrillic("... !!!")


class TestNormalizeCyrillicSpacing:
    def test_english_passes_through(self) -> None:
        text = "Hello world, how are you today?"
        assert normalize_cyrillic_spacing(text) == text

    def test_correct_cyrillic_passes_through(self) -> None:
        text = "Привіт, як справи? Я добре."
        assert normalize_cyrillic_spacing(text) == text

    def test_empty_text(self) -> None:
        assert normalize_cyrillic_spacing("") == ""

    def test_no_cyrillic_passes_through(self) -> None:
        text = "12345 !@#$%"
        assert normalize_cyrillic_spacing(text) == text

    def test_run_on_basic(self) -> None:
        run_on = "Будьласка.Дляменецеважливийзапит"
        result = normalize_cyrillic_spacing(run_on)
        assert " " in result
        assert "Будь" in result
        assert "ласка" in result
        assert "мене" in result
        assert "важливий" in result
        assert "." in result

    def test_run_on_preserves_punctuation(self) -> None:
        run_on = "Привіт,яксправи?Добре."
        result = normalize_cyrillic_spacing(run_on)
        assert "," in result
        assert "?" in result
        assert "." in result

    def test_mixed_english_cyrillic(self) -> None:
        text = "Hello, " + "це" * 20 + " world"
        result = normalize_cyrillic_spacing(text)
        assert "Hello," in result
        assert "world" in result

    def test_already_correct_not_modified(self) -> None:
        text = "Я хочу створити файл з назвою тест."
        result = normalize_cyrillic_spacing(text)
        assert result == text

    def test_preposition_merging_fixed(self) -> None:
        run_on = "язнающовихочуцезробити"
        result = normalize_cyrillic_spacing(run_on)
        assert "я" in result
        assert "знаю" in result
        assert "що" in result
        assert "хочу" in result
        assert "це" in result
        assert "зробити" not in _COMMON_UK_WORDS_SET
        assert " " in result


_COMMON_UK_WORDS_SET: set[str] = set()
try:
    from src.voice.tts.normalize import _COMMON_UK_WORDS as _W

    _COMMON_UK_WORDS_SET = _W
except ImportError:
    pass


def test_dictionary_has_core_words() -> None:
    for word in ("я", "ти", "він", "не", "так", "ні", "це", "що", "як"):
        assert word in _COMMON_UK_WORDS_SET, f"Missing core word: {word}"


def test_dictionary_excludes_budlaska_compound() -> None:
    assert "будьласка" not in _COMMON_UK_WORDS_SET, (
        "будьласка must NOT be in the dictionary — it should be split into будь+ласка"
    )
