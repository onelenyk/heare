"""The prompt budget: which turns the model gets to see."""

from __future__ import annotations

from heare.prompt.budget import DEFAULT_TOKENS, fit


def _text(tokens: int) -> str:
    return "x" * (tokens * 4)


def test_fit_drops_the_oldest_pairs_until_the_estimate_fits():
    turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": _text(100)}
        for i in range(10)
    ]

    system, kept = fit(_text(100), turns, limit=500)

    assert system == _text(100)
    assert kept == turns[-4:]


def test_fit_is_a_no_op_when_the_limit_is_large():
    turns = [
        {"role": "user", "content": "привіт"},
        {"role": "assistant", "content": "вітаю"},
    ]

    system, kept = fit("система", turns, limit=DEFAULT_TOKENS)

    assert system == "система"
    assert kept == turns


def test_fit_keeps_the_last_two_turns_even_when_they_alone_exceed_the_limit():
    turns = [
        {"role": "user", "content": _text(1000)},
        {"role": "assistant", "content": _text(1000)},
    ]

    _, kept = fit("", turns, limit=10)

    assert kept == turns


def test_fit_never_changes_the_system_string():
    system = _text(2000)

    got, _ = fit(system, [{"role": "user", "content": "те" * 100}], limit=10)

    assert got == system


def test_fit_handles_an_empty_conversation():
    system, kept = fit("система", [], limit=10)

    assert system == "система"
    assert kept == []
