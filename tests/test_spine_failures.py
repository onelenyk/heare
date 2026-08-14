"""Chaos / acceptance battery for src/spine: infrastructure keeps failing,
the conversation must not crash or wedge.

Every collaborator on SpineLoop is an injected callable — that seam is
the failure-injection point here, same as in test_spine_loop.py. Fakes
(FakeAudio, ScriptedVAD, FakeEvent, FakeKind, FakeAssembler) are reused
from that file rather than duplicated; test_spine_loop.py itself is not
modified.

Two scenarios could not be tested exactly as specified, and are softened
with a documented reason at the point of the softening:

* TTS raises on first sentence (#5) — loop.py has no per-sentence
  try/except around synthesise(): any exception there propagates all the
  way out of respond() (same path already covered by
  test_respond_unmutes_even_when_tts_fails in test_spine_loop.py), before
  the "reply" text is ever assembled or appended to history. So instead
  of asserting the reply text survives, this file asserts the honest
  alternative: no *half-written* reply corrupts history, and the next
  turn recovers cleanly.
* Slow playback wedge (#10) — loop.py's drain-timeout is a literal
  60.0s, too slow to run in a fast test file. loop.py is not modified;
  instead asyncio.wait_for is monkeypatched with a wrapper that shrinks
  only a 60.0s call to 0.05s and passes any other timeout through
  unchanged.

All tests are offline, fast, and rely on asyncio_mode = "auto"
(pyproject.toml) — no explicit @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from src.spine.loop import SpineLoop
from src.spine.sentences import sentences as split_sentences
from src.spine.turn import TurnAssembler
from tests.test_spine_loop import (
    FakeAssembler,
    FakeAudio,
    FakeEvent,
    FakeKind,
    ScriptedVAD,
)


# -- shared loop builder -------------------------------------------------


def _base_loop(**overrides) -> SpineLoop:
    """A SpineLoop with working defaults everywhere; override the piece
    under test. Uses the real sentence splitter (not a one-line fake) so
    whitespace/empty-stream tests reflect production filtering."""

    async def transcribe(pcm: bytes):
        class _T:
            text = "текст"

        return _T()

    async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
        yield "Гаразд."

    async def synthesise(text: str) -> AsyncIterator[bytes]:
        yield b"PCM:" + text.encode()

    kwargs: dict = dict(
        audio=FakeAudio(),
        vad=ScriptedVAD({}),
        assembler=FakeAssembler(),
        transcribe=transcribe,
        stream_chat=lambda m: stream_chat(m),
        split_sentences=split_sentences,
        synthesise=synthesise,
        poll_interval=0.01,
    )
    kwargs.update(overrides)
    return SpineLoop(**kwargs)


class _NoopToolbox:
    """A toolbox that is present (so stream_events is used) but whose
    execute() must never be called in tests that use it."""

    @property
    def schemas(self) -> list[dict]:
        return []

    async def execute(self, name: str, arguments: dict) -> str:
        raise AssertionError("no tool_call event was emitted")


# -- 1. STT fails N times then recovers ----------------------------------


async def test_stt_fails_twice_then_recovers_and_loop_stays_alive() -> None:
    audio = FakeAudio()
    assembler = FakeAssembler()
    vad = ScriptedVAD(
        {
            1: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640),
            2: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640),
            3: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640),
        }
    )

    calls = {"n": 0}

    async def flaky_transcribe(pcm: bytes):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ConnectionError("stt unreachable")

        class _T:
            text = "нарешті почули"

        return _T()

    loop = _base_loop(audio=audio, vad=vad, assembler=assembler)
    loop.transcribe = flaky_transcribe

    run = asyncio.create_task(loop.run())
    for _ in range(3):
        audio.input_frames.put_nowait(b"\x00" * 640)

    async def _all_three_landed() -> None:
        while len(assembler.fragments) < 3:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_all_three_landed(), timeout=2.0)

    async def _spoken() -> None:
        while not audio.played:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_spoken(), timeout=2.0)

    assert not run.done(), "run() must survive repeated STT failures"
    run.cancel()

    # First two utterances released the turn with an empty transcript
    # (STT failure must not leave the assembler holding it open); the
    # third one, once STT recovered, produced a spoken reply.
    assert assembler.fragments == ["", "", "нарешті почули"]
    assert loop.history[-2] == {"role": "user", "content": "нарешті почули"}
    assert audio.played, "the recovered utterance must reach the speaker"


# -- 2. STT hangs forever on one call ------------------------------------


async def test_stt_hangs_injected_turn_still_answered_and_assembler_self_heals() -> (
    None
):
    """The stt worker gets stuck forever inside transcribe() for one
    utterance. Two independent claims: (a) _converse() is a separate task,
    so a loop.inject()-ed turn still gets answered while the stt worker is
    blocked; (b) TurnAssembler's own 10*hold_s dead-STT defence means the
    assembler does not stay open forever either, once enough silence has
    passed on its (fake, controllable) clock."""
    audio = FakeAudio()
    clock = {"t": 0.0}

    def fake_clock() -> float:
        return clock["t"]

    assembler = TurnAssembler(hold_s=1.0, clock=fake_clock)
    vad = ScriptedVAD(
        {
            1: FakeEvent(FakeKind("start")),
            2: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640),
        }
    )

    hang_forever = asyncio.Event()  # never set -> transcribe never returns

    async def stuck_transcribe(pcm: bytes):
        await hang_forever.wait()
        class _T:  # pragma: no cover - never reached
            text = "ніколи не долетить"

        return _T()

    loop = _base_loop(audio=audio, vad=vad, assembler=assembler)
    loop.transcribe = stuck_transcribe

    run = asyncio.create_task(loop.run())
    audio.input_frames.put_nowait(b"\x00" * 640)  # -> VAD start
    audio.input_frames.put_nowait(b"\x00" * 640)  # -> VAD end, stt worker hangs

    # Give _listen/_stt_worker a beat to actually enter the hung await.
    await asyncio.sleep(0.05)
    assert assembler.open, "speech_started() must have opened the turn"

    # (a) An injected turn must still be answered by the independent
    # converse task while the stt worker sits blocked.
    await loop.inject("привіт з дашборду")

    async def _spoken() -> None:
        while not audio.played:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_spoken(), timeout=2.0)
    assert not run.done(), "converse must not depend on the stuck stt worker"
    assert loop.history[-2] == {"role": "user", "content": "привіт з дашборду"}

    # (b) Advance the assembler's own clock past 10*hold_s with no
    # transcript ever having landed, and confirm poll() lets go instead
    # of holding the turn open forever.
    clock["t"] += 10 * 1.0 + 0.01
    assert assembler.poll() is None
    assert not assembler.open, "dead-stt defence must release the stuck turn"

    run.cancel()


# -- 3. LLM stream dies mid-reply -----------------------------------------


async def test_llm_stream_dies_mid_reply_leaves_no_half_written_turn() -> None:
    loop = _base_loop(audio=None)

    async def dying_stream(messages: list[dict]) -> AsyncIterator[str]:
        yield "Зачекай"
        yield ", я "
        raise ConnectionError("stream reset by peer")

    loop.stream_chat = lambda m: dying_stream(m)

    # respond() itself has no try/except around the stream — only
    # _converse() catches and logs (see loop.py _converse). Calling
    # respond() directly, as this test does, must therefore propagate.
    with pytest.raises(ConnectionError):
        await loop.respond("розкажи щось довге", speak=False)

    assert loop.history == [{"role": "user", "content": "розкажи щось довге"}], (
        "no half-appended assistant message may survive a dead stream"
    )

    async def healthy_stream(messages: list[dict]) -> AsyncIterator[str]:
        yield "Все гаразд."

    loop.stream_chat = lambda m: healthy_stream(m)
    reply = await loop.respond("а тепер?", speak=False)

    assert reply == "Все гаразд."
    assert loop.history[-2:] == [
        {"role": "user", "content": "а тепер?"},
        {"role": "assistant", "content": "Все гаразд."},
    ]


# -- 4. LLM returns only whitespace ---------------------------------------


async def test_llm_whitespace_only_reply_leaves_no_history_entry() -> None:
    audio = FakeAudio()
    loop = _base_loop(audio=audio)

    async def blank_stream(messages: list[dict]) -> AsyncIterator[str]:
        yield "   "
        yield "\t\n  "

    loop.stream_chat = lambda m: blank_stream(m)

    reply = await loop.respond("є хтось?")

    assert reply == ""
    assert loop.history == [{"role": "user", "content": "є хтось?"}]
    assert audio.played == [], "nothing worth saying must not reach the speaker"

    async def real_stream(messages: list[dict]) -> AsyncIterator[str]:
        yield "Так, тут."

    loop.stream_chat = lambda m: real_stream(m)
    reply2 = await loop.respond("ехо")

    assert reply2 == "Так, тут."
    assert audio.played, "the follow-up turn must actually speak"


# -- 5. TTS raises on first sentence (softened, see module docstring) ----


async def test_tts_raises_no_corrupted_reply_survives_and_next_turn_speaks() -> None:
    audio = FakeAudio()
    loop = _base_loop(audio=audio)

    async def broken_synth(text: str) -> AsyncIterator[bytes]:
        raise RuntimeError("tts down")
        yield b""  # pragma: no cover - makes this an async generator

    loop.synthesise = broken_synth

    with pytest.raises(RuntimeError):
        await loop.respond("скажи щось голосом")

    assert audio.mute_input is False, "finally must still unmute on tts failure"
    assert loop.history == [{"role": "user", "content": "скажи щось голосом"}], (
        "a reply that never finished speaking must not land in history"
    )

    async def working_synth(text: str) -> AsyncIterator[bytes]:
        yield b"PCM:" + text.encode()

    loop.synthesise = working_synth
    reply = await loop.respond("а це?")

    assert reply
    assert audio.played, "next turn must actually speak once tts recovers"


# -- 6. TTS yields nothing --------------------------------------------------


async def test_tts_empty_generator_completes_without_hanging() -> None:
    audio = FakeAudio()
    loop = _base_loop(audio=audio)

    async def silent_synth(text: str) -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover - makes this an async generator

    loop.synthesise = silent_synth

    reply = await asyncio.wait_for(loop.respond("тихо, будь ласка"), timeout=2.0)

    assert reply, "reply text is still produced even if nothing was voiced"
    assert loop.history[-1] == {"role": "assistant", "content": reply}
    assert audio.played == [], "an empty synth stream must play nothing, not hang"

    async def working_synth(text: str) -> AsyncIterator[bytes]:
        yield b"PCM:" + text.encode()

    loop.synthesise = working_synth
    reply2 = await loop.respond("а тепер щось чути?")

    assert reply2
    assert audio.played, "next turn must speak once synthesise yields bytes"


# -- 7 & 8. side channels (persist, usage) must never break the turn -----


class RaisingPersist:
    def log_user_turn(self, text: str) -> int:
        raise OSError("disk full")

    def log_agent_reply(self, text: str, turn_id: int) -> None:
        raise OSError("disk full")


class RaisingUsage:
    def llm(self, model: str, inp: int, out: int) -> None:
        raise OSError("usage db locked")

    def tts(self, chars: int) -> None:
        raise OSError("usage db locked")


@pytest.mark.parametrize(
    "attr, value",
    [("persist", RaisingPersist()), ("usage", RaisingUsage())],
    ids=["persist-log-user-turn-raises", "usage-recorder-raises-every-call"],
)
async def test_side_channel_failures_never_break_the_conversation(attr, value) -> None:
    audio = FakeAudio()
    loop = _base_loop(audio=audio)
    loop.toolbox = _NoopToolbox()

    async def events(messages, tools):
        yield {"type": "delta", "text": "Порядок"}
        yield {"type": "delta", "text": " денний."}
        # Exercises usage.llm too, when usage is the collaborator under test.
        yield {"type": "usage", "model": "x", "input_tokens": 1, "output_tokens": 1}

    loop.stream_events = events
    setattr(loop, attr, value)

    reply = await loop.respond("як справи?")
    assert reply == "Порядок денний."
    assert audio.played, "tts must still run even if the side channel is broken"

    # Repeated failures must not degrade the loop over time.
    reply2 = await loop.respond("а тепер?")
    assert reply2 == "Порядок денний."


# -- 9. toolbox.execute raises ---------------------------------------------


async def test_toolbox_execute_raises_speaks_ukrainian_fallback() -> None:
    audio = FakeAudio()
    loop = _base_loop(audio=audio)

    class BrokenToolbox:
        @property
        def schemas(self) -> list[dict]:
            return [{"type": "function", "function": {"name": "delegate"}}]

        async def execute(self, name: str, arguments: dict) -> str:
            raise RuntimeError("tool backend unreachable")

    loop.toolbox = BrokenToolbox()

    async def events(messages, tools):
        yield {"type": "delta", "text": "Гаразд, "}
        yield {
            "type": "tool_call",
            "id": "1",
            "name": "delegate",
            "arguments": '{"task": "щось"}',
        }

    loop.stream_events = events

    reply = await loop.respond("зроби щось")

    assert "Не вийшло виконати дію." in reply
    spoken = b"".join(audio.played).decode()
    assert "Не вийшло виконати дію." in spoken, "the fallback must be spoken too"

    # loop keeps working: drop back to the plain chat path for the next turn.
    loop.stream_events = None
    reply2 = await loop.respond("а це просто розмова")
    assert reply2


# -- 10. slow playback wedge (softened, see module docstring) ------------


async def test_slow_playback_wedge_is_bounded(monkeypatch) -> None:
    real_wait_for = asyncio.wait_for

    async def shrunk_wait_for(aw, timeout=None):
        if timeout == 60.0:
            timeout = 0.05
        return await real_wait_for(aw, timeout=timeout)

    monkeypatch.setattr(asyncio, "wait_for", shrunk_wait_for)

    class NeverDrainsAudio(FakeAudio):
        """playing never goes False; stop_playback() (called once the
        drain wait times out) clears .played the same way the real
        device's queue would be dropped — so the queued-before-timeout
        claim is checked via the drop count it returns, not via .played
        surviving afterwards."""

        @property
        def playing(self) -> bool:
            return True

    audio = NeverDrainsAudio()
    loop = _base_loop(audio=audio)

    real_stop_playback = audio.stop_playback
    dropped_counts: list[int] = []
    audio.stop_playback = lambda: dropped_counts.append(real_stop_playback()) or dropped_counts[-1]

    reply = await asyncio.wait_for(loop.respond("це затягнеться"), timeout=2.0)

    assert reply, "reply text is produced even though playback never drained"
    assert dropped_counts and dropped_counts[0] > 0, (
        "audio was queued (and then dropped by the timeout) before the drain gave up"
    )

    # loop keeps working: another turn completes too, bounded the same way.
    reply2 = await asyncio.wait_for(loop.respond("і це теж"), timeout=2.0)
    assert reply2
