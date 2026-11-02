"""A key is lent to the code that was allowed, and to nothing else."""

from __future__ import annotations

from heare import keys
from heare.api.grants import Grants


def test_a_yes_holds_for_that_code_and_survives_a_restart(tmp_path):
    path = tmp_path / "grants.json"
    grants = Grants(path)
    assert not grants.allowed("voice@mac", "GROQ_API_KEY", "a")
    grants.ask("voice@mac", "GROQ_API_KEY", "a", "1")
    assert grants.answer("voice@mac", "GROQ_API_KEY", True)
    assert Grants(path).allowed("voice@mac", "GROQ_API_KEY", "a")


def test_new_code_asks_again_and_says_it_is_an_update(tmp_path):
    grants = Grants(tmp_path / "grants.json")
    grants.ask("voice@mac", "GROQ_API_KEY", "a", "1")
    grants.answer("voice@mac", "GROQ_API_KEY", True)
    assert not grants.allowed("voice@mac", "GROQ_API_KEY", "b")
    grants.ask("voice@mac", "GROQ_API_KEY", "b", "2")
    assert grants.waiting[("voice@mac", "GROQ_API_KEY")]["update"]


def test_only_what_was_asked_can_be_allowed(tmp_path):
    grants = Grants(tmp_path / "grants.json")
    assert not grants.answer("voice@mac", "GROQ_API_KEY", True)
    assert not grants.allowed("voice@mac", "GROQ_API_KEY", "")


def test_a_no_takes_the_old_yes_and_does_not_ask_again(tmp_path):
    grants = Grants(tmp_path / "grants.json")
    grants.ask("voice@mac", "GROQ_API_KEY", "a", "1")
    grants.answer("voice@mac", "GROQ_API_KEY", True)
    grants.ask("voice@mac", "GROQ_API_KEY", "b", "2")
    grants.answer("voice@mac", "GROQ_API_KEY", False)
    grants.ask("voice@mac", "GROQ_API_KEY", "b", "2")
    assert not grants.waiting
    assert not grants.allowed("voice@mac", "GROQ_API_KEY", "a")


def test_a_loan_stays_in_the_process_and_can_be_taken_back():
    keys.lend("GROQ_API_KEY", "lent")
    assert keys.get("GROQ_API_KEY") == "lent"
    keys.lend("GROQ_API_KEY", None)
    assert keys.get("GROQ_API_KEY") != "lent"
