"""A tool call belongs to the wire; the words shown to the person never
carry a model's control tokens (16 November 2026)."""

from __future__ import annotations

from heare.tokens import Cleaner, clean


def test_plain_words_are_left_alone():
    assert clean("Дивлюсь. Далі буде.") == "Дивлюсь. Далі буде."


def test_a_control_token_is_removed():
    assert clean('a<|DSLM|> calls<|DSLM|> b') == "a calls b"


def test_a_full_width_token_is_removed_too():
    assert clean("a<｜DSLM｜>b") == "ab"


def test_a_token_split_across_pieces_is_still_removed():
    cleaner = Cleaner()

    out = cleaner.feed("Привіт <|DS")
    out += cleaner.feed("LM|> світ")
    out += cleaner.flush()

    assert out == "Привіт  світ"


def test_an_opener_that_never_closes_is_kept():
    cleaner = Cleaner()

    assert cleaner.feed("a <|b") == "a "
    assert cleaner.flush() == "<|b"


def test_a_long_run_that_only_starts_like_a_token_is_text():
    long = "<|" + "x" * 100

    assert clean(long) == long
