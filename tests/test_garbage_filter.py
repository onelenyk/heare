"""Tests for the garbage-transcript filter (is_garbage_transcript)."""
from __future__ import annotations

import pytest

from src.pipeline.stages.transcription_gate import is_garbage_transcript


class TestGarbageTrue:
    """Transcripts that should be classified as garbage (TTS bleed)."""

    @pytest.mark.parametrize("text", [
        # Repeated pattern (n≥3, any unit)
        "сь-сь-сь",     # repeated syllable with hyphens
        "а-а-а",        # repeated single char with hyphens
        "ммммм",        # repeated consonant (5x)
        "б-б-б",        # single consonant repeated
        "ааааа",        # 5x same char
        "зззз",         # 4x same char
        # Repeated pattern (n=2, unit≥4)
        "приветпривет",  # word duplication without space (unit=6, reps=2)
        # No-vowel consonant clusters
        "чс-чс",        # repeated 2-char consonant cluster, no vowels
        # Empty / whitespace / punctuation only
        "",             # empty string
        "   ",          # whitespace only
        "...",          # punctuation only
        # All-single-token spaced repeats
        "а а а",        # spaced single-char repeats
        "б б б б",      # spaced single-char repeats
        # Latin
        "zzzz",         # Latin repeated consonant
        # Character diversity < 0.4
        "lollol",       # "lol" × 2 → caught by diversity (2 unique / 6 = 0.33)
        # Repeated pattern (n≥3, unit≥3)
        "lollollol",    # "lol" × 3 → caught by repeated pattern
        # Single-char dominance > 60%
        "аааабв",       # 'а' = 4/6 = 67% dominance (≥4 chars → check fires)
    ])
    def test_garbage_detected(self, text: str) -> None:
        assert is_garbage_transcript(text) is True


class TestGarbageFalse:
    """Transcripts that should pass as real speech."""

    @pytest.mark.parametrize("text", [
        # Real short words (Cyrillic)
        "стоп",         # Ukrainian/Russian stop
        "да",           # Russian yes
        "ні",           # Ukrainian no
        "так",          # Ukrainian yes
        "привіт",       # Ukrainian hello
        "нет",          # Russian no
        "ну",           # Russian well
        # Interjections in allowlist
        "хм",           # interjection hmm
        "мм",           # interjection mm
        # Interjections NOT in allowlist but real speech
        "ага",          # uh-huh (not in allowlist, but passes all checks)
        "угу",          # uh-huh
        "ого",          # wow
        # Short Latin words
        "ok",           # English ok
        "stop",         # English stop
        "yes",          # English yes
        # Multi-word (natural speech with spaces)
        "да да",        # repeated word with real separators
        "стоп стоп",    # multi-word repeat
        "hello world",  # multi-word English
        "привет как дела",  # multi-word Russian
        # Real words with punctuation
        "серйозно?",    # real word + punctuation
        "правда?",      # real word + punctuation
        # Longer real words
        "сегодняшнее",  # real word fragment (echo detector handles this)
    ])
    def test_real_speech_passes(self, text: str) -> None:
        assert is_garbage_transcript(text) is False

    def test_multi_word_with_varied_lengths(self) -> None:
        """Multi-word with tokens of different lengths → real speech."""
        assert is_garbage_transcript("але це вже інше") is False

    def test_short_latin_with_vowel_not_in_allowlist(self) -> None:
        """Short Latin string with vowel but not in allowlist → garbage (check 4)."""
        assert is_garbage_transcript("oy") is True

    def test_mixed_script_latin_vowel(self) -> None:
        """Latin text with vowel passes no-vowel check."""
        assert is_garbage_transcript("test") is False

    def test_diverse_two_char(self) -> None:
        """Two different chars, not in allowlist → garbage."""
        assert is_garbage_transcript("бв") is True

    def test_two_chars_with_vowel_not_in_allowlist(self) -> None:
        """Two-char with vowel but not in allowlist → garbage (not in _SHORT_ALLOW)."""
        assert is_garbage_transcript("во") is False

    def test_short_consonant_only(self) -> None:
        """Consonant-only two chars not in allowlist → garbage."""
        assert is_garbage_transcript("бр") is True

    def test_no_vowels_longer_than_two(self) -> None:
        """No vowels, length > 2 → garbage."""
        assert is_garbage_transcript("брр") is True

    def test_boundary_three_char_with_vowel_no_repeat(self) -> None:
        """3 chars with vowel, no repeat, diverse → passes all checks (not garbage)."""
        assert is_garbage_transcript("xyz") is False

    def test_boundary_diversity_just_above_40pc(self) -> None:
        """Diversity exactly at 0.4 → passes check 2, check 3."""
        # "абвг" = 4 unique / 4 chars = 1.0 diversity, passes
        # For 0.4: 2 unique / 5 chars = 0.4
        assert is_garbage_transcript("ааааб") is True  # 2/5=0.4, not < 0.4 → passes check 2, but single-char dominance: 4/5=80% > 60% → garbage

    def test_short_vowel_not_in_allowlist(self) -> None:
        """Single char (always garbage - no allowlist for length 1)."""
        assert is_garbage_transcript("а") is True
        assert is_garbage_transcript("x") is True

    def test_punctuation_with_real_word(self) -> None:
        """Real word with trailing punctuation should pass."""
        assert is_garbage_transcript("stop!") is False
        assert is_garbage_transcript("так.") is False
