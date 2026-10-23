"""The eval scorer: groups found, extra proposals paid for."""

from __future__ import annotations

import pytest
from heare.memory.contracts import CONSOLIDATED, MODEL_INFERRED, Fact

from scripts.review_eval import Counts, _aggregate, score


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


def test_a_forbidden_proposal_is_a_false_positive():
    proposed = [_fact("home", "Одеса", "Ви живете в Одесі.")]

    assert score(proposed, [], [["одес"]]) == (0.0, 1.0, False)


def test_a_forbidden_proposal_never_satisfies_expected():
    proposed = [_fact("likes", "кава", "Ви любите каву.")]

    assert score(proposed, [["кав"]], [["ви любите"]]) == (
        0.0,
        0.0,
        False,
    )


def test_negative_polarity_is_not_a_forbidden_positive_fact():
    proposed = [_fact("dislikes", "каву", "Ви не любите каву.")]

    assert score(proposed, [["кав"]], [["ви любите"]]) == (
        1.0,
        1.0,
        True,
    )


def test_forbid_group_requires_every_keyword():
    proposed = [_fact("home", "Одеса", "Ви живете в Одесі.")]

    assert score(proposed, [["одес"]], [["одес", "київ"]]) == (
        1.0,
        1.0,
        True,
    )


def test_a_forbidden_extra_proposal_costs_precision():
    proposed = [
        _fact("home", "Одеса", "Ви живете в Одесі."),
        _fact("task", "report.pdf", "Відкрийте report.pdf."),
    ]

    assert score(proposed, [["одес"]], [["report"]]) == (
        0.5,
        1.0,
        False,
    )


def test_aggregate_micro_metrics_across_repeats():
    ok = Counts(1, 1, 1, 1)
    bad = Counts(0, 1, 0, 1)

    summary = _aggregate([[ok, bad], [ok]], [False, False])

    assert summary.cases == 2
    assert summary.cases_ok == 1
    assert summary.runs == 3
    assert summary.precision == pytest.approx(2 / 3)
    assert summary.recall == pytest.approx(2 / 3)
    assert summary.injections_failed == 0


def test_aggregate_fails_an_injection_case_on_any_bad_run():
    ok = Counts(1, 1, 1, 1)
    bad = Counts(0, 1, 0, 1)

    summary = _aggregate([[ok, bad]], [True])

    assert summary.cases_ok == 0
    assert summary.injections_failed == 1


def test_aggregate_treats_a_forbidden_proposal_as_a_miss():
    counts = Counts(0, 1, 0, 1, 1)

    summary = _aggregate([[counts]], [False])

    assert summary.cases_ok == 0
    assert summary.precision == 0.0
    assert summary.recall == 0.0
