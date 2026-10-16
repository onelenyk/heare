"""The chat body: it prints what the brain says, and nothing else."""

from __future__ import annotations

from heare.chat import _show


def test_a_said_event_is_printed_with_the_name(capsys):
    _show({"kind": "said", "text": "Готово."}, "Дока")
    assert "Дока: Готово." in capsys.readouterr().out


def test_a_cannot_event_is_noted(capsys):
    _show({"kind": "cannot", "why": "не вмію"}, "Дока")
    assert "не вмію" in capsys.readouterr().out


def test_an_unrelated_event_is_silent(capsys):
    _show({"kind": "phase", "phase": "idle"}, "Дока")
    assert capsys.readouterr().out == ""
