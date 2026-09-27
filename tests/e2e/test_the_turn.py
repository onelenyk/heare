"""The turn and the wake gate — how fragments join and the window grows.

A person does not speak in finished sentences, and neither should the
assistant finish answering after the first fragment arrives. A pause mid-
thought is not a thought finished, and a longer window mid-conversation
is not a threat.

These are the tests that prove the ASSEMBLED path: real loop, real gate,
real assembler, the model scripted. Unit tests prove rules in isolation;
these prove the conversation: a person's hesitant voice is heard as one
thought, an unaddressed turn does not creep the window open, and a long
conversation earns the user's silence.
"""

from __future__ import annotations

import asyncio

import pytest

from src.spine.wake import WakeGate
from tests.e2e.room import close_room, open_room, until

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.e2e_text]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


# ── scenario 1: unfinished thought waits longer ──────────────────────


async def test_an_unfinished_thought_is_waited_for(tmp_path) -> None:
    """«А потім я…» — and a breath — is not a question yet. It is held for
    the continuation hold, so the rest joins it and the model is asked
    once, with the whole thought. A finished sentence after the same pause
    is its own turn. Before this, a breath at a comma split the turn and
    the person was answered mid-thought (4ceb3e3, 81a3cf6).

    Through the real loop: holds set small but real, fragments delivered
    the way the recogniser delivers them, and what the model was asked is
    read back from the room.
    """
    room = await open_room(
        tmp_path,
        settings={
            "spine_turn_hold_seconds": 0.3,
            "spine_turn_continuation_hold_seconds": 1.5,
            "wake_required": False,
        },
    )
    try:
        room.will_say("Добре, записую.", "Зараз дев'ята.", "Завтра субота.")

        def fragment(text: str) -> None:
            room.loop.assembler.speech_started()
            room.loop.assembler.transcript(text)

        def asked() -> list[str]:
            return [
                next(m["content"] for m in reversed(p) if m["role"] == "user")
                for p in room.prompts
            ]

        fragment("а потім я")
        await asyncio.sleep(0.6)  # past the normal hold, inside the long one
        assert asked() == [], "answered in the middle of a thought"
        fragment("напишу нотатку.")
        await until(lambda: len(asked()) == 1)
        assert "а потім я" in asked()[0] and "напишу нотатку" in asked()[0]

        fragment("Котра година?")
        await until(lambda: len(asked()) == 2)
        await asyncio.sleep(0.6)
        fragment("А завтра?")
        await until(lambda: len(asked()) == 3)
        assert "Котра година?" in asked()[1] and "завтра" not in asked()[1]
    finally:
        await close_room(room)


async def test_the_wake_window_grows_with_consecutive_accepted_turns(room) -> None:
    """A single addressed turn lasts on the short leash (45 seconds). A real
    conversation — several turns in a row — earns a longer one, multiplied
    by the consecutive accepted turns, capped at max_window_s (180 seconds).

    What a person hears: an exchange that runs four turns with no wake word
    still fits in the grown window even after a long pause.
    """
    # Create fake clocks so we can step through time without sleeping.
    mono_clock = FakeClock(0.0)
    wall_clock = FakeClock(1_700_000_000.0)

    # Replace the gate with one using our fake clocks.
    from src.spine.wake_phrases import wake_phrases, own_name

    room.loop.wake = WakeGate(
        wake_phrases(room.settings, name=own_name(room.settings)),
        window_s=45.0,
        max_window_s=180.0,
        required=True,
        clock=mono_clock,
        wall=wall_clock,
    )

    # Queue three answers for three turns.
    room.will_say("Здоров.", "Сім утра.", "Думаю, припинимо.")

    # Turn 1: addressed — wakes the gate at t=0, window is 45s.
    result = await room.hears("Дока, привіт")
    assert result == "Здоров."
    assert room.loop.wake.awake is True
    assert room.loop.wake.window_s == 45.0

    # Advance to t=10, still well within the first window.
    mono_clock.set(10.0)
    wall_clock.set(1_700_000_000.0 + 10.0)

    # Turn 2: addressed, refreshes the window. Now streak=2, window=90s.
    result = await room.hears("А котра година?")
    assert result == "Сім утра."
    assert room.loop.wake.window_s == 90.0

    # Advance to t=60 — well past the first 45s window, but within the
    # grown 90s window that started at t=10.
    mono_clock.set(60.0)
    wall_clock.set(1_700_000_000.0 + 60.0)

    # Turn 3: UNADDRESSED (no wake word), but within the grown window.
    # Must be accepted.
    result = await room.hears("Дякую, розуміється.")
    assert result == "Думаю, припинимо."
    assert room.loop.wake.awake is True


async def test_wake_window_cap_is_enforced(room) -> None:
    """The window grows but never past max_window_s (180 seconds by default).
    Many turns in a row do not earn an infinite window.

    What a person hears: even in a long conversation, there is a
    practical limit — you cannot walk away for four hours and come back
    without the wake word.
    """
    mono_clock = FakeClock(0.0)
    wall_clock = FakeClock(1_700_000_000.0)

    # Copy the original room's gate phrases (resolved correctly at build time)
    original_phrases = room.loop.wake._phrases

    room.loop.wake = WakeGate(
        original_phrases,
        window_s=45.0,
        max_window_s=180.0,
        required=True,
        clock=mono_clock,
        wall=wall_clock,
    )

    # Queue many answers to build up a long streak.
    for _ in range(6):
        room.will_say("Ок.")

    # Build up a 5-turn conversation: window would be 45*5=225s, but capped at 180s.
    for i in range(5):
        await room.hears(f"Дока, питання {i}")
        mono_clock.set(float(i + 1) * 10.0)
        wall_clock.set(1_700_000_000.0 + (i + 1) * 10.0)

    # Window should be at the cap.
    assert room.loop.wake.window_s == 180.0

    # Advance 40s from the last accepted turn (t=50), reaching t=90.
    # Still within the 180s window that started at t=50.
    mono_clock.set(90.0)
    wall_clock.set(1_700_000_000.0 + 90.0)

    # Unaddressed turn is still accepted.
    result = await room.hears("Та, дякую.")
    assert result == "Ок."

    # Advance another 100s to t=190 — now past the 180s window that started at t=90.
    # 190 - 90 = 100, which is less than 180. So it should still be accepted.
    # Let's advance to t=271 instead: 271 - 90 = 181 > 180.
    mono_clock.set(271.0)
    wall_clock.set(1_700_000_000.0 + 271.0)

    # Unaddressed turn must be rejected (will not reach the model).
    before = len(room.heard())
    await room.overhears("Та, дякую.")
    after = len(room.heard())
    assert before == after, "rejected turn must not be heard"


async def test_rejected_turn_does_not_extend_the_window(room) -> None:
    """A turn the gate rejects (unaddressed, window closed) must not
    accidentally wake or extend the window. A film playing in the background
    must not hold the gate open for 4.5 minutes just because it happens to
    be transcribed.

    What a person hears: the assistant stays asleep after the window closes.
    Noise or a stray transcription does not give it back standing to listen.
    """
    mono_clock = FakeClock(0.0)
    wall_clock = FakeClock(1_700_000_000.0)

    # Copy the original room's gate phrases (resolved correctly at build time)
    original_phrases = room.loop.wake._phrases

    room.loop.wake = WakeGate(
        original_phrases,
        window_s=45.0,
        max_window_s=180.0,
        required=True,
        clock=mono_clock,
        wall=wall_clock,
    )

    room.will_say("Привіт.", "Чув, тепер розбудився.")

    # Wake the gate at t=0.
    result = await room.hears("Дока, привіт")
    assert result == "Привіт."
    assert room.loop.wake.awake is True

    # Advance to t=50, past the 45s window.
    mono_clock.set(50.0)
    wall_clock.set(1_700_000_000.0 + 50.0)

    # Try to send an unaddressed turn — should be rejected and not
    # reach the model.
    before = len(room.heard())
    await room.overhears("Фільм на тлі: розповідь про кіно.")
    after = len(room.heard())
    assert before == after, "rejected turn must not be heard"

    # The gate is still asleep. The window was NOT extended by the
    # rejected turn — `_last_accept` is still at t=0, not at t=50.
    assert room.loop.wake.awake is False

    # To prove this, try an unaddressed turn at t=95 (45s after the
    # rejected turn). It should still be rejected because the window
    # never opened past t=45 — only a new wake phrase wakes it.
    mono_clock.set(95.0)
    wall_clock.set(1_700_000_000.0 + 95.0)

    before = len(room.heard())
    await room.overhears("Щось ще без фрази.")
    after = len(room.heard())
    assert before == after, "must stay asleep, rejected turn did not extend window"

    # But a phrase DOES wake it — it uses the second queued answer.
    result = await room.hears("Дока, проснувся?")
    assert result == "Чув, тепер розбудився."
    assert room.loop.wake.awake is True


class FakeClock:
    """A simple injectable clock that moves only when set() is called.

    Used with WakeGate and TurnAssembler to test timing-dependent
    behaviour without sleeping or depending on real system clocks.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def set(self, t: float) -> None:
        self._t = t
