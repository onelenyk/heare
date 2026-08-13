"""Integration tests for the spine conductor — everything faked.

The loop imports no sibling module, so these tests define the contracts
in miniature: if a fake satisfies the test but the real module does not,
the module violated the contract, not the loop.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncIterator

from src.spine.loop import SpineLoop


# -- fakes -------------------------------------------------------------


class FakeAudio:
    def __init__(self) -> None:
        self.input_frames: asyncio.Queue = asyncio.Queue()
        self.mute_input = False
        self.played: list[bytes] = []
        self.mute_log: list[bool] = []

    def play(self, pcm: bytes) -> None:
        self.mute_log.append(self.mute_input)
        self.played.append(pcm)

    def stop_playback(self) -> int:
        dropped = sum(len(p) for p in self.played)
        self.played.clear()
        return dropped

    @property
    def playing(self) -> bool:
        return False  # queued audio "drains" instantly in tests


@dataclass
class FakeKind:
    value: str


@dataclass
class FakeEvent:
    kind: FakeKind
    utterance: bytes = b""


class ScriptedVAD:
    """Emits a scripted event on the nth feed() call."""

    def __init__(self, script: dict[int, FakeEvent]) -> None:
        self.script = script
        self.calls = 0

    def feed(self, frame: bytes) -> FakeEvent | None:
        self.calls += 1
        return self.script.get(self.calls)


class FakeAssembler:
    """Closes a turn immediately after each non-empty transcript."""

    def __init__(self) -> None:
        self.fragments: list[str] = []
        self.speech_starts = 0
        self._ready: list[str] = []

    def speech_started(self) -> None:
        self.speech_starts += 1

    def transcript(self, text: str, *, speech_resumed: bool = False) -> None:
        self.fragments.append(text)
        self.resumed_flags: list[bool] = getattr(self, "resumed_flags", [])
        self.resumed_flags.append(speech_resumed)
        if text.strip():
            self._ready.append(text.strip())

    def poll(self) -> str | None:
        return self._ready.pop(0) if self._ready else None


async def _one_sentence(deltas: AsyncIterator[str]) -> AsyncIterator[str]:
    yield "".join([d async for d in deltas])


def _make_loop(
    audio: FakeAudio | None,
    vad=None,
    assembler=None,
    reply_deltas: list[str] | None = None,
    stt_text: str = "привіт",
    stt_raises: bool = False,
) -> SpineLoop:
    async def transcribe(pcm: bytes):
        if stt_raises:
            raise RuntimeError("stt down")

        class _T:
            text = stt_text

        return _T()

    async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
        for d in reply_deltas or ["Вітаю", "!"]:
            yield d

    async def synthesise(text: str) -> AsyncIterator[bytes]:
        yield b"PCM:" + text.encode()

    return SpineLoop(
        audio=audio,
        vad=vad or ScriptedVAD({}),
        assembler=assembler or FakeAssembler(),
        transcribe=transcribe,
        stream_chat=lambda m: stream_chat(m),
        split_sentences=_one_sentence,
        synthesise=synthesise,
        poll_interval=0.01,
    )


# -- respond -----------------------------------------------------------


async def test_respond_returns_reply_and_updates_history() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio, reply_deltas=["Він", " працює."])

    reply = await loop.respond("як демон?")

    assert reply == "Він працює."
    assert loop.history == [
        {"role": "user", "content": "як демон?"},
        {"role": "assistant", "content": "Він працює."},
    ]
    assert audio.played == [b"PCM:" + "Він працює.".encode()]


async def test_respond_mutes_mic_while_speaking_and_unmutes_after() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio)

    await loop.respond("скажи щось")

    assert audio.mute_log and all(audio.mute_log), "mic must be muted during playback"
    assert audio.mute_input is False, "mic must be unmuted after the reply"


async def test_respond_without_audio_only_returns_text() -> None:
    loop = _make_loop(audio=None)
    reply = await loop.respond("текстовий режим")
    assert reply == "Вітаю!"


async def test_respond_unmutes_even_when_tts_fails() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio)

    async def broken_tts(text: str) -> AsyncIterator[bytes]:
        raise RuntimeError("no voice")
        yield b""  # pragma: no cover

    loop.synthesise = broken_tts
    try:
        await loop.respond("зламай рот")
    except RuntimeError:
        pass
    assert audio.mute_input is False


# -- the full ear → mouth path ----------------------------------------


async def test_speech_becomes_a_spoken_reply() -> None:
    audio = FakeAudio()
    vad = ScriptedVAD(
        {
            2: FakeEvent(FakeKind("start")),
            5: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640),
        }
    )
    assembler = FakeAssembler()
    loop = _make_loop(audio, vad=vad, assembler=assembler)

    run = asyncio.create_task(loop.run())
    for _ in range(6):
        audio.input_frames.put_nowait(b"\x00" * 640)

    async def _spoken() -> None:
        while not audio.played:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_spoken(), timeout=2.0)
    run.cancel()

    assert assembler.speech_starts == 1
    assert assembler.fragments == ["привіт"]
    assert audio.played, "the reply must reach the speaker"
    assert loop.history[0] == {"role": "user", "content": "привіт"}


async def test_slow_stt_sees_that_speech_resumed() -> None:
    """A START arriving after an utterance's END must reach the assembler
    as speech_resumed=True on that utterance's transcript — otherwise a
    slow Groq call closes the turn mid-sentence (reviewer finding #1)."""
    audio = FakeAudio()
    vad = ScriptedVAD(
        {
            1: FakeEvent(FakeKind("start")),
            2: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640),
            3: FakeEvent(FakeKind("start")),  # user resumed before STT landed
        }
    )
    assembler = FakeAssembler()

    stt_gate = asyncio.Event()

    async def slow_transcribe(pcm: bytes):
        await stt_gate.wait()

        class _T:
            text = "перша фраза"

        return _T()

    loop = _make_loop(audio, vad=vad, assembler=assembler)
    loop.transcribe = slow_transcribe

    run = asyncio.create_task(loop.run())
    for _ in range(3):
        audio.input_frames.put_nowait(b"\x00" * 640)

    async def _two_starts() -> None:
        while assembler.speech_starts < 2:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_two_starts(), timeout=2.0)
    stt_gate.set()  # STT returns only after the second START

    async def _fragment() -> None:
        while not assembler.fragments:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_fragment(), timeout=2.0)
    run.cancel()

    assert assembler.resumed_flags == [True], (
        "transcript must know that newer speech started after its utterance"
    )


async def test_stt_failure_releases_the_turn_instead_of_wedging() -> None:
    audio = FakeAudio()
    vad = ScriptedVAD({1: FakeEvent(FakeKind("end"), utterance=b"\x00" * 640)})
    assembler = FakeAssembler()
    loop = _make_loop(audio, vad=vad, assembler=assembler, stt_raises=True)

    run = asyncio.create_task(loop.run())
    audio.input_frames.put_nowait(b"\x00" * 640)

    async def _released() -> None:
        while assembler.fragments != [""]:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_released(), timeout=2.0)
    run.cancel()
    assert not audio.played, "no reply may be spoken for a failed transcription"


# -- parity features: wake gate, tools, barge-in -----------------------


class FakeWake:
    def __init__(self, accept: bool) -> None:
        self.accept = accept
        self.asked: list[str] = []

    def accepts(self, text: str) -> bool:
        self.asked.append(text)
        return self.accept


class FakeToolbox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    @property
    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "delegate"}}]

    async def execute(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return "Прийнято, роблю."


async def test_asleep_turn_never_reaches_the_llm() -> None:
    audio = FakeAudio()
    assembler = FakeAssembler()
    assembler.transcript("просто розмова в кімнаті")
    loop = _make_loop(audio, assembler=assembler)
    loop.wake = FakeWake(accept=False)

    chat_called = []

    async def spy_chat(messages):
        chat_called.append(messages)
        yield "не має статись"

    loop.stream_chat = lambda m: spy_chat(m)

    run = asyncio.create_task(loop.run())
    await asyncio.sleep(0.1)
    run.cancel()

    assert loop.wake.asked == ["просто розмова в кімнаті"]
    assert chat_called == [], "asleep turn must not reach the LLM"
    assert not audio.played


async def test_tool_call_is_executed_and_ack_spoken() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio)
    toolbox = FakeToolbox()
    loop.toolbox = toolbox

    async def events(messages, tools):
        assert tools == toolbox.schemas
        yield {"type": "delta", "text": "Зараз "}
        yield {"type": "delta", "text": "гляну."}
        yield {"type": "tool_call", "id": "1", "name": "delegate",
               "arguments": '{"task": "подивитись вкладки"}'}

    loop.stream_events = events

    reply = await loop.respond("подивись вкладки")

    assert toolbox.calls == [("delegate", {"task": "подивитись вкладки"})]
    assert "Прийнято, роблю." in reply
    spoken = b"".join(audio.played).decode()
    assert "Прийнято" in spoken, "the ack must be spoken too"


async def test_broken_tool_arguments_do_not_crash_the_turn() -> None:
    loop = _make_loop(audio=None)
    toolbox = FakeToolbox()
    loop.toolbox = toolbox

    async def events(messages, tools):
        yield {"type": "tool_call", "id": "1", "name": "delegate",
               "arguments": "{невалідний json"}

    loop.stream_events = events
    reply = await loop.respond("зроби щось", speak=False)
    assert toolbox.calls == [("delegate", {})]
    assert reply  # the ack still lands in the reply


class DuplexAEC:
    active = True
    def __init__(self) -> None:
        self.far: list[bytes] = []
    def process(self, frame: bytes) -> bytes:
        return frame
    def push_far(self, pcm: bytes) -> None:
        self.far.append(pcm)


async def test_full_duplex_does_not_mute_and_feeds_far_end() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio)
    aec = DuplexAEC()
    loop.aec = aec

    await loop.respond("скажи щось")

    assert audio.mute_log and not any(audio.mute_log), (
        "with an active AEC the mic must stay open while speaking"
    )
    assert aec.far, "spoken audio must reach the canceller as far-end"


async def test_interrupted_reply_stops_feeding_the_speaker() -> None:
    audio = FakeAudio()
    loop = _make_loop(audio, reply_deltas=["Перше речення. ", "Друге речення."])
    loop.aec = DuplexAEC()

    async def two_sentences(deltas):
        buf = "".join([d async for d in deltas])
        for s in buf.split(". "):
            if loop is not None:
                yield s
            loop._interrupted = True  # user barges in after sentence one

    loop.split_sentences = two_sentences
    reply = await loop.respond("розкажи довго")

    assert len(audio.played) == 1, "speech after the interrupt must not play"
    assert "Друге" in reply, "the full reply still lands in history"
