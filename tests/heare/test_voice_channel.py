"""The real Voice around a fake device: frames in, reports out.

No microphone, no recogniser, no network. The device is a queue and a
few flags; the recogniser and synthesiser are replaced by recorders.
"""

from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
from typing import Any

import httpx

from heare.channels.voice import channel
from heare.channels.voice.channel import Voice, open_voice
from heare.channels.voice.stt import Transcript
from heare.channels.voice.turn import TurnAssembler
from heare.voice import HttpPort, _Speaker


SILENCE = b"\x00" * 640
LOUD = struct.pack("<h", 8000) * 320  # -12 dBFS, 20 ms at 16 kHz


def _settings(**values: Any) -> SimpleNamespace:
    defaults = {
        "language": "uk",
        "listen": "judge",
        "keep_heard": False,
        "spine_vad_stop_ms": 40,
        "spine_turn_hold_seconds": 0.01,
        "spine_turn_continuation_hold_seconds": 0.02,
        "voice": "",
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class FakeDevice:
    """What AudioIO looks like to the voice, without a sound card."""

    def __init__(self) -> None:
        self.input_frames: asyncio.Queue[bytes] = asyncio.Queue()
        self.mute_input = False
        self.mute_input_user = False
        self.mute_output = False
        self.mute_output_user = False
        self.output_volume = 1.0
        self.playing = False
        self.input_open = True
        self.silent = 0.0
        self.restart_calls = 0
        self.restart_result = True
        self.reopened = asyncio.Event()
        self._gap = False

    def silent_for(self) -> float:
        return self.silent

    zeros = 0.0

    def zeros_for(self) -> float:
        return self.zeros

    async def restart_input(self) -> bool:
        self.restart_calls += 1
        if self.restart_result:
            self.silent = 0.0
            self.reopened.set()
        return self.restart_result

    def take_input_gap(self) -> bool:
        gap, self._gap = self._gap, False
        return gap

    def play(self, pcm: bytes) -> None:
        pass

    def stop_playback(self) -> int:
        return 0

    async def stop(self) -> None:
        pass


class FakeAEC:
    active = True

    def process(self, frame: bytes) -> bytes:
        return frame


class FakePort:
    def __init__(self) -> None:
        self.started_calls: list[str] = []
        self.heard_calls: list[Any] = []
        self.nothing_calls: list[str] = []
        self.notes: list[tuple[str, object]] = []
        self.overheard_calls: list[tuple[str, str]] = []
        self.said_overs: list[tuple[str, str, float, bool]] = []
        self.verdict = False

    def started(self, channel_name: str) -> None:
        self.started_calls.append(channel_name)

    def heard(self, message) -> None:
        self.heard_calls.append(message)

    def nothing(self, channel_name: str) -> None:
        self.nothing_calls.append(channel_name)

    def note(self, channel_name: str, key: str, value: object) -> None:
        self.notes.append((key, value))

    def overheard(self, channel_name: str, text: str) -> None:
        self.overheard_calls.append((channel_name, text))

    def said_over(self, channel_name: str, text: str, speech_ms: float,
                  long: bool) -> bool:
        self.said_overs.append((channel_name, text, speech_ms, long))
        return self.verdict


def _speech_frames() -> list[bytes]:
    return [SILENCE] * 10 + [LOUD] * 20 + [SILENCE] * 5


async def _until(check, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if check():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("timed out waiting")


async def _stop(*tasks: asyncio.Task) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _feed(device: FakeDevice) -> None:
    for frame in _speech_frames():
        device.input_frames.put_nowait(frame)


async def _transcribing(monkeypatch, text: str = "привіт") -> None:
    async def fake_transcribe(pcm: bytes, **kwargs):
        return Transcript(text=text, language="uk")

    monkeypatch.setattr(channel, "transcribe", fake_transcribe)


async def test_a_finished_utterance_reaches_the_brain_as_heard(monkeypatch):
    await _transcribing(monkeypatch)
    device, port = FakeDevice(), FakePort()
    voice = Voice(device, _settings(), watch_s=0.05)
    running = asyncio.create_task(voice.run(port))
    await _feed(device)

    await _until(lambda: port.heard_calls)
    await _stop(running)

    assert port.started_calls == ["voice"]
    assert [m.text for m in port.heard_calls] == ["привіт"]
    assert port.nothing_calls == []


async def test_a_voice_over_the_reply_is_a_said_over_not_a_turn(monkeypatch):
    await _transcribing(monkeypatch, "далі")
    for verdict, expected in ((False, 0), (True, 1)):
        device, port = FakeDevice(), FakePort()
        device.playing = True
        port.verdict = verdict
        voice = Voice(device, _settings(), aec=FakeAEC(), watch_s=0.05)
        running = asyncio.create_task(voice.run(port))
        await _feed(device)

        await _until(lambda: port.said_overs)
        if expected:
            await _until(lambda: port.heard_calls)
        await _stop(running)

        assert port.said_overs, "the core was never asked what the voice meant"
        _, text, speech_ms, long = port.said_overs[0]
        assert text == "далі"
        assert speech_ms > 0
        assert long is False, "a short nod is not an interruption"
        assert len(port.heard_calls) == expected


async def test_the_voice_loop_reports_a_dead_mic_and_reopens_it():
    device, port = FakeDevice(), FakePort()
    device.silent = 30.0
    voice = Voice(device, _settings(), watch_s=0.01)
    running = asyncio.create_task(voice.run(port))

    await _until(lambda: device.restart_calls)
    await asyncio.sleep(0.08)
    await _stop(running)

    deaf = [note for note in port.notes if str(note[0]).startswith("deaf:")]
    assert len(deaf) == 1, "one outage is one remark"
    assert device.restart_calls == 1, "one reopen, not a storm"


async def test_a_microphone_the_system_keeps_shut_is_said_once_and_not_reopened():
    """5 November: the stream ran, macOS handed it only zeros (a stale
    permission), no orange dot, and heare said nothing for three days."""
    device, port = FakeDevice(), FakePort()
    device.zeros = 30.0
    voice = Voice(device, _settings(), watch_s=0.01)
    running = asyncio.create_task(voice.run(port))

    await _until(lambda: any(str(n[0]).startswith("deaf:") for n in port.notes))
    await asyncio.sleep(0.08)
    await _stop(running)

    deaf = [note for note in port.notes if str(note[0]).startswith("deaf:")]
    assert len(deaf) == 1, "one outage is one remark"
    assert "Input" in str(deaf[0]) or "Вхід" in str(deaf[0]), "it says where to fix it: the input level first"
    assert device.restart_calls == 0, "a reopen is only another shut stream"


async def test_a_brain_restart_mid_reply_starts_a_new_utterance(monkeypatch):
    async def fake_synthesise(text: str, *, voice: str):
        yield b"\x00\x00" * 100

    monkeypatch.setattr(channel, "synthesise", fake_synthesise)
    device = FakeDevice()
    voice = Voice(device, _settings(), watch_s=0.05)
    speaker = _Speaker(voice, idle_s=0.1)

    speaker.feed({"channel": "voice", "kind": "reply", "text": "Привіт."})
    await _until(lambda: voice.mouth._live)
    first = voice.mouth.last
    assert device.mute_input is True

    await speaker.reset()
    assert voice.mouth._live == [], "the abandoned utterance was not closed"
    assert device.mute_input is False, "the microphone stayed muted"

    speaker.feed({"channel": "voice", "kind": "reply", "text": "Друга."})
    await _until(lambda: voice.mouth._live)
    assert voice.mouth.last is not first, "the reply was glued to the old one"

    speaker.feed({"channel": "voice", "kind": "said", "text": "Друга."})
    await _until(lambda: not voice.mouth._live)
    assert device.mute_input is False
    await speaker.cancel()


async def test_four_silent_syntheses_fall_back_to_the_system_voice(monkeypatch):
    calls = 0

    async def silent(text: str, *, voice: str):
        nonlocal calls
        calls += 1
        for _ in ():
            yield b""

    monkeypatch.setattr(channel, "synthesise", silent)
    monkeypatch.setattr(channel, "SYNTH_RETRY_S", (0.0, 0.0, 0.0, None))
    monkeypatch.setattr(channel, "_system_voice", lambda text: b"SYSTEM")
    voice = Voice(object(), _settings())

    chunks = [chunk async for chunk in voice._synthesise("Привіт.")]
    assert calls == 4
    assert chunks == [b"SYSTEM"]

    monkeypatch.setattr(channel, "_system_voice", lambda text: b"")
    assert [chunk async for chunk in voice._synthesise("Привіт.")] == []


def test_a_slow_transcript_does_not_split_a_turn():
    now = [0.0]
    assembler = TurnAssembler(hold_s=0.8, clock=lambda: now[0],
                              wall=lambda: now[0])

    assembler.transcript("Купи")
    now[0] = 1.0
    assembler.speech_started()
    now[0] = 9.0
    assert assembler.poll() is None, "the turn closed while STT was still working"
    assert assembler.open is True

    assembler.transcript("молоко")
    now[0] = 11.0
    assert assembler.poll() == "Купи молоко", "one sentence became two turns"


async def test_a_non_json_said_over_verdict_does_not_kill_recognition(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    port = HttpPort(
        httpx.Client(transport=httpx.MockTransport(handler)), "http://brain")
    assert port.said_over("voice", "ага", 200.0, False) is False

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async_port = HttpPort(client, "http://brain")
        assert await async_port.said_over("voice", "ага", 200.0, False) is False

    await _transcribing(monkeypatch, "привіт")
    voice = Voice(object(), _settings())
    voice._port = port
    recognising = asyncio.create_task(voice._recognise())
    over = channel._Over(heard_ms=200.0, speech_ms=200.0)
    loud = LOUD * 20
    voice._jobs.put_nowait((loud, 0, over, 0))
    voice._jobs.put_nowait((loud, 0, None, 0))

    await _until(lambda: voice.assembler._fragments)
    assert not recognising.done(), "recognition died on the bad verdict"
    assert voice.assembler._fragments == ["привіт"]
    await _stop(recognising)


async def test_a_boot_without_a_microphone_stays_alive(monkeypatch, tmp_path):
    from heare.channels.voice import device as device_module

    class DeafDevice(FakeDevice):
        def __init__(self) -> None:
            super().__init__()
            self.input_open = False
            self.silent = 30.0

        async def start(self) -> None:
            raise RuntimeError("no microphone")

        async def restart_input(self) -> bool:
            self.restart_calls += 1
            return False

    monkeypatch.setattr(device_module, "AudioIO", DeafDevice)
    settings = _settings(heard_dir=str(tmp_path / "heard"))
    voice = await open_voice(settings, watch_s=0.01)
    port = FakePort()
    running = asyncio.create_task(voice.run(port))

    await _until(lambda: voice.device.restart_calls)
    await asyncio.sleep(0.08)
    await _stop(running)

    assert isinstance(voice, Voice)
    assert voice.device.input_open is False
    assert voice.device.restart_calls == 1, "the retry was a crash loop"
    assert [key for key, _ in port.notes if str(key).startswith("deaf:")]


async def test_a_reopened_microphone_drops_stale_frames_and_notes_the_gap(
        monkeypatch, tmp_path):
    import sys

    from heare.channels.voice.device import AudioIO

    streams: list[Any] = []

    class Stream:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False
            self.closed = False
            streams.append(self)

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def close(self) -> None:
            self.closed = True

    fake_sounddevice = SimpleNamespace(RawInputStream=lambda **kwargs: Stream())
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    device = AudioIO()
    device._loop = asyncio.get_running_loop()
    device.input_frames.put_nowait(b"stale")
    device.input_frames.put_nowait(b"stale")

    assert await device.restart_input() is True

    assert streams[0].started
    assert device.take_input_gap() is True, "the hole was not announced"
    assert device.input_frames.empty(), "stale frames were fed to the new stream"


async def test_the_long_flag_is_frozen_at_the_end_of_the_over_utterance(monkeypatch):
    gate = asyncio.Event()

    async def slow_transcribe(pcm: bytes, **kwargs):
        await gate.wait()
        return Transcript(text="далі", language="uk")

    monkeypatch.setattr(channel, "transcribe", slow_transcribe)
    device, port = FakeDevice(), FakePort()
    device.playing = True
    voice = Voice(device, _settings(), aec=FakeAEC(), watch_s=0.05)
    running = asyncio.create_task(voice.run(port))

    for frame in _speech_frames():
        device.input_frames.put_nowait(frame)
    await _until(lambda: voice._over is not None and voice._over.speech_ms is not None)
    frozen = voice._over.speech_ms

    # The person (or the room) keeps talking while STT is still working.
    for _ in range(50):
        device.input_frames.put_nowait(LOUD)
    await asyncio.sleep(0.05)
    gate.set()

    await _until(lambda: port.said_overs)
    await _stop(running)

    _, _, speech_ms, long = port.said_overs[0]
    assert speech_ms == frozen
    assert long is False, "frames after END turned a nod into an interruption"


def test_the_courtesy_window_is_short_enough_that_noise_cannot_live_in_it():
    from heare.channels.voice import channel

    assert channel.COURTESY_WINDOW_S <= 10
