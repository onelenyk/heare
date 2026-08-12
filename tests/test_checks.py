"""What a scenario counts as a reply.

Every check here decides whether a run is red or green without a person
in the room, so a check that measures the wrong thing is worse than no
check: it teaches you to distrust the colour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.pipeline import checks


@dataclass
class FakeRun:
    heard: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    spoken: list[str] = field(default_factory=list)
    heard_itself: int = 0
    barge_in_ms: float | None = None
    bot_utterances: int = 0
    events: list[Any] = field(default_factory=list)


def test_two_replies_run_together_still_count_as_two() -> None:
    """The delegated answer arrived while the acknowledgement was still
    being spoken, so the speaker played fifteen unbroken seconds. Counted
    by silences that is one reply, and the run failed with both replies
    sitting in the transcript."""
    run = FakeRun(
        spoken=[
            "Перевіряю вільне місце на диску, за мить скажу результат.",
            "Вільного місця близько 473 гігабайти.",
        ],
        bot_utterances=1,
    )

    assert checks.replies(at_least=2)(run) == []


def test_acknowledging_twice_is_still_caught() -> None:
    """The bug the upper bound exists for: it says "гляну" twice and
    never speaks the answer."""
    run = FakeRun(spoken=["Секунду, гляну.", "Зараз гляну.", "Ще дивлюсь."])

    assert checks.replies(at_least=1, at_most=2)(run)


def test_saying_nothing_fails_a_scenario_that_expects_an_answer() -> None:
    assert checks.replies(at_least=1)(FakeRun())


def test_audio_stands_in_when_no_words_were_captured() -> None:
    """If the words were never collected, sound is the only evidence
    there is — and it is better than declaring silence."""
    run = FakeRun(spoken=[], bot_utterances=2)

    assert checks.replies(at_least=2)(run) == []


# ── speech meant for nobody ───────────────────────────────────────────


def test_answering_the_room_is_a_failure() -> None:
    run = FakeRun(spoken=["Так, звісно!"])

    assert checks.stays_silent()(run)


def test_silence_passes() -> None:
    assert checks.stays_silent()(FakeRun(heard=["якісь балачки"])) == []


def test_a_reply_with_no_words_captured_still_counts_as_speaking() -> None:
    """Half of the reasons this project's checks were wrong came from one
    signal going missing and the run reading as clean."""
    assert checks.stays_silent()(FakeRun(bot_utterances=1))


# ── the rest ──────────────────────────────────────────────────────────


def test_an_interruption_that_never_fired_is_a_failure() -> None:
    assert checks.barge_in_under(1800)(FakeRun())
    assert checks.barge_in_under(1800)(FakeRun(barge_in_ms=1040.0)) == []
    assert checks.barge_in_under(1800)(FakeRun(barge_in_ms=2400.0))


def test_the_delegated_answer_must_reach_the_ears() -> None:
    """It found "488 GB free" and said "секунду, гляну" a second time."""
    run = FakeRun(spoken=["Секунду, гляну.", "На диску 473 гігабайти вільно."])

    assert checks.eventually_says("гігабайти")(run) == []
    assert checks.eventually_says("мегабайти")(run)


def test_every_reason_is_reported_not_only_the_first() -> None:
    run = FakeRun(heard_itself=2)

    failures = checks.run_checks(
        [checks.never_hears_itself(), checks.replies(at_least=1)], run
    )
    assert len(failures) == 2
