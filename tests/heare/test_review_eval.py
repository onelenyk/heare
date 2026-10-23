"""The eval scorer: groups found, extra proposals paid for."""

from __future__ import annotations

from heare.memory.contracts import CONSOLIDATED, MODEL_INFERRED, Fact

from scripts.review_eval import score


def _fact(predicate: str, value: str, text: str) -> Fact:
    return Fact(
        subject="owner",
        predicate=predicate,
        value=value,
        text=text,
        source=CONSOLIDATED,
        trust=MODEL_INFERRED,
    )


def test_one_proposed_fact_matching_one_group():
    proposed = [_fact("diet", "м’ясо", "Ви не їсте м’ясо.")]

    assert score(proposed, [["м'яс"]]) == (1.0, 1.0, True)


def test_one_proposed_fact_can_satisfy_two_groups():
    proposed = [_fact("prefers", "зелений чай", "Ви любите зелений чай.")]

    assert score(proposed, [["зелен"], ["чай"]]) == (1.0, 1.0, True)


def test_an_extra_proposal_costs_precision():
    proposed = [
        _fact("name", "Олена", "Вас звати Олена."),
        _fact("pet", "Мурчик", "У вас є кіт Мурчик."),
    ]

    assert score(proposed, [["олен"]]) == (0.5, 1.0, False)


def test_missing_fact_costs_recall():
    assert score([], [["олен"]]) == (0.0, 0.0, False)


def test_nothing_expected_and_nothing_proposed_passes():
    assert score([], []) == (1.0, 1.0, True)


def test_a_proposal_when_none_is_expected_fails():
    proposed = [_fact("name", "Олена", "Вас звати Олена.")]

    assert score(proposed, []) == (0.0, 1.0, False)
