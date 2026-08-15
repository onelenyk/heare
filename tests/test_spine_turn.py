"""Tests for TurnAssembler: one clock, fragments joined into one turn.

Uses a fake clock (a mutable float in a list) so timing is deterministic —
no sleeps, no asyncio.
"""

from __future__ import annotations

from src.spine.turn import TurnAssembler


def make_clock(start: float = 0.0):
    """Return (clock_fn, advance_fn) sharing a mutable time value."""
    t = [start]

    def clock() -> float:
        return t[0]

    def advance(delta: float) -> None:
        t[0] += delta

    return clock, advance


HOLD = 1.0


def test_single_fragment_closes_only_after_hold() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("fragment one.")
    assert ta.open is True

    advance(HOLD * 0.5)
    assert ta.poll() is None

    advance(HOLD * 0.5)  # now at exactly HOLD since the fragment
    assert ta.poll() == "fragment one."

    # closed turn: polling again yields nothing, and stays closed
    assert ta.poll() is None
    assert ta.open is False


def test_two_fragments_join_into_one_turn() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("фрагмент один.")
    advance(HOLD * 0.5)
    ta.transcript("фрагмент два.")

    # not yet quiet for a full hold since the second fragment
    advance(HOLD * 0.5)
    assert ta.poll() is None

    advance(HOLD * 0.5)  # now quiet for a full hold since fragment two
    assert ta.poll() == "фрагмент один. фрагмент два."


def test_speech_in_progress_keeps_turn_open_past_deadline() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("fragment one.")
    ta.speech_started()

    advance(HOLD * 3)  # well past the deadline, but still "speaking"
    assert ta.poll() is None
    assert ta.open is True

    ta.transcript("fragment two.")
    advance(HOLD * 0.99)
    assert ta.poll() is None  # not quite a hold since the new fragment

    advance(HOLD * 0.01)
    assert ta.poll() == "fragment one. fragment two."


def test_empty_transcript_clears_speaking_without_touching_fragments() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("fragment one.")
    ta.speech_started()
    ta.transcript("")  # cough: clears speaking, adds nothing

    advance(HOLD)
    assert ta.poll() == "fragment one."


def test_whitespace_fragments_never_open_a_turn() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("")
    ta.transcript("   ")
    ta.transcript("\t\n")
    assert ta.open is False

    advance(HOLD * 10)
    assert ta.poll() is None
    assert ta.open is False


def test_stale_speech_started_with_prior_fragment_closes_anyway() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("fragment one.")
    ta.speech_started()
    assert ta.open is True

    # STT died: no transcript, no further speech_started, for >10*hold
    advance(HOLD * 10 + 0.001)
    assert ta.poll() == "fragment one."
    assert ta.open is False


def test_stale_speech_started_without_fragment_does_not_wedge_open() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.speech_started()
    assert ta.open is True  # speech is in progress, even with no fragment yet

    advance(HOLD * 10 + 0.001)
    assert ta.poll() is None
    assert ta.open is False


def test_reset_drops_everything() -> None:
    clock, advance = make_clock()
    ta = TurnAssembler(hold_s=HOLD, clock=clock)

    ta.transcript("fragment one.")
    ta.speech_started()
    assert ta.open is True

    ta.reset()
    assert ta.open is False

    advance(HOLD * 10)
    assert ta.poll() is None
    assert ta.open is False

    # assembler is fully usable again after reset
    ta.transcript("fresh start.")
    advance(HOLD)
    assert ta.poll() == "fresh start."


def test_speech_resumed_keeps_the_turn_open_past_a_slow_stt() -> None:
    """A slow transcript for utterance 1 lands while the user is already
    speaking utterance 2: it must not clear the speaking flag, or the
    turn closes mid-sentence with half the thought (reviewer finding #1)."""
    t = [0.0]
    a = TurnAssembler(hold_s=1.0, clock=lambda: t[0])

    a.speech_started()             # utterance 1
    t[0] = 1.0                     # (its END happened; STT in flight)
    a.speech_started()             # utterance 2 begins before STT lands
    t[0] = 1.5
    a.transcript("перша фраза.", speech_resumed=True)   # slow STT arrives

    t[0] = 3.0                     # well past hold — but user still talking
    assert a.poll() is None, "turn must stay open while speech is in progress"

    a.transcript("друга фраза.")    # utterance 2 transcribed normally
    t[0] = 4.1
    assert a.poll() == "перша фраза. друга фраза."


# -- a pause mid-thought is not the end of a turn ----------------------


def test_sounds_unfinished_reads_the_words() -> None:
    from src.spine.turn import sounds_unfinished

    # still coming: no closing mark, a trailing conjunction, an ellipsis
    for text in (
        "я думаю що треба зробити",
        "тобто якщо ми візьмемо цей варіант і",
        "ну типу",
        "давай зробимо так...",
        "мені треба щоб воно працювало тому",
    ):
        assert sounds_unfinished(text) is True, text

    # finished: a full sentence with a terminal mark
    for text in (
        "Скільки вільного місця на диску?",
        "Зроби це.",
        "Стоп!",
        "Дякую.",
    ):
        assert sounds_unfinished(text) is False, text


def test_unfinished_fragment_waits_longer_than_a_finished_one() -> None:
    t = [0.0]
    a = TurnAssembler(hold_s=1.0, continuation_hold_s=3.0, clock=lambda: t[0])

    a.transcript("я хочу щоб ти")     # hangs on a conjunction
    t[0] = 1.5                         # past the normal hold
    assert a.poll() is None, "a thought in progress must not be answered"

    a.transcript("перевірив диск.")   # now it is finished
    t[0] = 2.6                         # 1.1s after the last fragment
    assert a.poll() == "я хочу щоб ти перевірив диск."


def test_finished_sentence_still_answers_promptly() -> None:
    t = [0.0]
    a = TurnAssembler(hold_s=1.0, continuation_hold_s=3.0, clock=lambda: t[0])

    a.transcript("Котра година?")
    t[0] = 1.05
    assert a.poll() == "Котра година?", "a complete question must not wait"
