"""The laws: the bright lines are code, not the model's mood."""

from __future__ import annotations

from heare import laws


def test_deleting_and_spending_pass_the_gate():
    assert laws.risky("delete")
    assert laws.risky("money")


def test_looking_does_not_pass_the_gate():
    assert not laws.risky("read")
    assert not laws.risky("think")
