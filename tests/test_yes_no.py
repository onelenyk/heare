"""Tests for parse_yes_no in src/decider.py."""
from __future__ import annotations

import pytest

from src.decider import parse_yes_no


@pytest.mark.parametrize(
    "text",
    [
        "так",
        "Так!",
        "ага",
        "давай",
        "зроби",
        "вперед",
        "окей",
        "yes",
        "yeah",
        "sure",
        "ok",
        "go",
        "  так  ",
        "красава",
    ],
)
def test_yes_variants(text: str) -> None:
    assert parse_yes_no(text) == "yes"


@pytest.mark.parametrize(
    "text",
    [
        "ні",
        "нет",
        "не треба",
        "не потрібно",
        "nevermind",
        "cancel",
        "stop",
        "skip",
        "abort",
        "не зараз",
    ],
)
def test_no_variants(text: str) -> None:
    assert parse_yes_no(text) == "no"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "можливо",
        "я не знаю",
        "maybe later actually",
        "розкажи детальніше",
    ],
)
def test_unclear(text: str) -> None:
    assert parse_yes_no(text) in {"unclear", "yes", "no"}
