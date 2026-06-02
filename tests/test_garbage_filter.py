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


class TestMixedScript:
    """Real speech mixing multiple scripts."""

    def test_mixed_cyrillic_latin(self) -> None:
        """Real speech mixing Cyrillic and Latin is common."""
        assert is_garbage_transcript("привіт hello") is False
        assert is_garbage_transcript("ok да") is False
        assert is_garbage_transcript("yes так") is False


class TestNumbers:
    """Numbers mixed with real words — should pass."""

    def test_numbers_with_words(self) -> None:
        """Numbers mixed with words are real speech."""
        assert is_garbage_transcript("2 стоп") is False
        assert is_garbage_transcript("ok 1") is False
        assert is_garbage_transcript("да 2") is False


class TestNumbersAsGarbage:
    """Pure number strings are STT noise."""

    def test_pure_numbers(self) -> None:
        """Pure number strings are likely STT noise."""
        assert is_garbage_transcript("12345") is True
        assert is_garbage_transcript("11111") is True
        assert is_garbage_transcript("121212") is True


class TestUnicodeEdgeCases:
    """Real words with unicode punctuation."""

    def test_unicode_punctuation(self) -> None:
        """Real words with unicode punctuation should pass."""
        assert is_garbage_transcript("привіт!") is False
        assert is_garbage_transcript("да?") is False
        assert is_garbage_transcript("stop...") is False
        assert is_garbage_transcript("ага!") is False


class TestVeryLongRepeated:
    """Very long repeated patterns are garbage."""

    def test_very_long_repeated(self) -> None:
        """Very long repeated patterns are garbage."""
        assert is_garbage_transcript("а" * 20) is True
        assert is_garbage_transcript("аб" * 10) is True
        assert is_garbage_transcript("привет" * 3) is True
        assert is_garbage_transcript("мммммммммммммммм") is True


class TestBoundaryConditions:
    """Precise threshold testing for diversity and dominance checks."""

    def test_diversity_exactly_at_04(self) -> None:
        """Diversity exactly at 0.4 should PASS (not < 0.4)."""
        # 2 unique / 5 total = 0.4 exactly, dominance = 3/5 = 0.6 (not > 0.6)
        assert is_garbage_transcript("ааабб") is False

    def test_diversity_just_below_04(self) -> None:
        """Diversity just below 0.4 should be GARBAGE."""
        # 2 unique / 6 total = 0.33 < 0.4
        assert is_garbage_transcript("аааабб") is True

    def test_dominance_exactly_60pc(self) -> None:
        """Dominance exactly at 60% should PASS (not > 0.6)."""
        # 3/5 = 0.6 exactly, n = 5 >= 4
        assert is_garbage_transcript("ааабв") is False

    def test_dominance_just_above_60pc(self) -> None:
        """Dominance just above 60% should be GARBAGE."""
        # 4/6 = 0.667 > 0.6
        assert is_garbage_transcript("аааабв") is True


class TestCaseSensitivity:
    """Case handling in garbage detection."""

    def test_uppercase_garbage(self) -> None:
        """Uppercase garbage should still be caught."""
        assert is_garbage_transcript("СС-СС-СС") is True
        assert is_garbage_transcript("ААААА") is True
        assert is_garbage_transcript("МММММ") is True

    def test_mixed_case_real_words(self) -> None:
        """Mixed case real words should pass."""
        assert is_garbage_transcript("Стоп") is False
        assert is_garbage_transcript("Да") is False
        assert is_garbage_transcript("OK") is False
        assert is_garbage_transcript("Stop") is False


class TestWhitespaceVariations:
    """Whitespace handling in garbage detection."""

    def test_leading_trailing_whitespace(self) -> None:
        """Whitespace around garbage should still be caught."""
        assert is_garbage_transcript("  сь-сь-сь  ") is True
        assert is_garbage_transcript("\tммммм\n") is True

    def test_multiple_spaces_between_tokens(self) -> None:
        """Multiple spaces between real tokens should pass."""
        assert is_garbage_transcript("да   да") is False
        assert is_garbage_transcript("стоп  стоп") is False


class TestRealInterjectionsOtherLanguages:
    """Non-English interjections that should pass as real speech."""

    def test_french_interjections(self) -> None:
        """French interjections that might appear."""
        assert is_garbage_transcript("oui") is False
        assert is_garbage_transcript("non") is False

    def test_other_latin_interjections(self) -> None:
        """Other Latin-script interjections."""
        assert is_garbage_transcript("hey") is False
        assert is_garbage_transcript("wow") is False


class TestAcronymsAndAbbreviations:
    """Acronyms and abbreviations in voice context."""

    def test_common_acronyms(self) -> None:
        """Common acronyms with vowels pass; no-vowel short ones are garbage."""
        assert is_garbage_transcript("LOL") is False  # has vowel O
        assert is_garbage_transcript("BRB") is True   # no vowels, 3 chars → garbage
        assert is_garbage_transcript("OK") is False   # in allowlist
        assert is_garbage_transcript("TV") is True    # no vowels, 2 chars, not in list


class TestHyphenatedRealWords:
    """Hyphenated real words should pass cleaning."""

    def test_hyphenated_words(self) -> None:
        """Hyphenated real words should pass."""
        assert is_garbage_transcript("кое-что") is False  # Russian "something"
        assert is_garbage_transcript("кто-то") is False   # Russian "someone"


class TestPartialWords:
    """Partial word fragments that are still real speech."""

    def test_partial_real_words(self) -> None:
        """Partial words with vowels should pass."""
        assert is_garbage_transcript("при") is False  # partial "привет", has vowels
        assert is_garbage_transcript("сто") is False  # partial "стоп", has vowel
        assert is_garbage_transcript("хел") is False  # partial "hello", has vowel


class TestRepetitionWithVariation:
    """Near-repetitions and variations in real speech."""

    def test_near_repetition_not_garbage(self) -> None:
        """Near-repetitions with variation are real speech."""
        assert is_garbage_transcript("дадада") is True   # "да" × 3 → garbage
        assert is_garbage_transcript("да нет") is False  # real "yes no"
        assert is_garbage_transcript("ага ага") is False  # real "uh-huh uh-huh"


class TestEmptyAndWhitespace:
    """Various empty and whitespace-only inputs."""

    def test_various_empty_inputs(self) -> None:
        """Various empty/whitespace inputs are garbage."""
        assert is_garbage_transcript("") is True
        assert is_garbage_transcript(" ") is True
        assert is_garbage_transcript("\t") is True
        assert is_garbage_transcript("\n") is True
        assert is_garbage_transcript("  \t  \n  ") is True


class TestCheck1RepeatedExact:
    """Exhaustive testing of Check 1 (repeated pattern detection)."""

    def test_repeated_pattern_reps3(self) -> None:
        """Pattern repeated exactly 3 times → garbage."""
        assert is_garbage_transcript("abcabcabc") is True  # "abc" × 3

    def test_repeated_pattern_reps2_unit_ge4(self) -> None:
        """Pattern repeated 2 times, unit ≥ 4 → garbage."""
        assert is_garbage_transcript("testtest") is True  # "test" × 2, unit_len = 4

    def test_repeated_pattern_reps2_unit_lt4(self) -> None:
        """Pattern repeated 2 times, unit < 4 → not garbage (falls through)."""
        assert is_garbage_transcript("dada") is False   # "da" × 2, unit_len = 2
        assert is_garbage_transcript("abcabc") is False  # "abc" × 2, unit_len = 3

    def test_repeated_hyphen_pattern(self) -> None:
        """Hyphen-separated repeated patterns → garbage after cleaning."""
        assert is_garbage_transcript("la-la-la") is True
        assert is_garbage_transcript("но-но-но") is True


class TestMultiTokenEdgeCases:
    """Edge cases with multiple space-separated tokens."""

    def test_multi_token_all_single_varied(self) -> None:
        """Multiple single-char tokens of different chars → passes."""
        assert is_garbage_transcript("а б в г") is False
        assert is_garbage_transcript("a b c d e") is False

    def test_multi_token_mixed_lengths(self) -> None:
        """Mixed-length tokens → real speech (not all single)."""
        assert is_garbage_transcript("а бв") is False

    def test_multi_token_all_single_but_repeated(self) -> None:
        """Multiple single-char tokens, all same → garbage (Check 1 catches)."""
        assert is_garbage_transcript("а а а") is True
        assert is_garbage_transcript("a a a a") is True


class TestCheck5NoVowels:
    """Edge cases for Check 5 (no-vowel detection)."""

    def test_no_vowels_three_chars(self) -> None:
        """3 chars with no vowels → garbage."""
        assert is_garbage_transcript("brr") is True
        assert is_garbage_transcript("trp") is True

    def test_no_vowels_four_chars(self) -> None:
        """4+ chars with no vowels and diverse chars → garbage (no vowel)."""
        assert is_garbage_transcript("brrr") is True
        assert is_garbage_transcript("трпр") is True

    def test_no_vowels_with_digits(self) -> None:
        """Digits mixed with consonants, no vowel → garbage."""
        assert is_garbage_transcript("br7") is True
        assert is_garbage_transcript("trp9") is True


class TestPunctuationOnly:
    """Strings that become empty after cleaning."""

    def test_only_punctuation(self) -> None:
        """Only non-alphanumeric characters → garbage."""
        assert is_garbage_transcript("!@#$%^&*()") is True
        assert is_garbage_transcript("---") is True
        assert is_garbage_transcript("???") is True
