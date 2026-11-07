"""The voice body: its port's payloads, its stream, and its refusal."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from heare import part
from heare.config import Settings
from heare.core import Message
from heare.channels.voice.utterance import Mouth
from heare.voice import HttpPort, _apply_saved, _follow, _Speaker, run


class Ears:
    """A voice that records what it was asked to say, and its controls."""

    def __init__(self) -> None:
        self.replies: list[list[str]] = []
        self.outcomes: list[str] = []
        self.paused = 0
        self.resumed = 0
        self.stopped = 0
        self.finished = asyncio.Event()
        self.controls = {"mic": True, "volume": 1.0}

    def set_control(self, name: str, value) -> None:
        if name not in self.controls:
            raise KeyError(name)
        self.controls[name] = value

    async def say(self, pieces) -> str:
        parts = []
        async for piece in pieces:
            parts.append(piece)
        self.replies.append(parts)
        self.outcomes.append("done")
        self.finished.set()
        return "done"

    def pause(self) -> None:
        self.paused += 1

    def resume(self) -> None:
        self.resumed += 1

    def stop(self) -> None:
        self.stopped += 1


def _port(handler) -> HttpPort:
    return HttpPort(httpx.Client(transport=httpx.MockTransport(handler)),
                    "http://brain")


def _event(kind: str, **data) -> str:
    return "data: " + json.dumps(
        {"kind": kind, "channel": "voice", **data}, ensure_ascii=False)


async def _lines(items) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_the_port_sends_each_report_exactly():
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "turn": False})

    port = _port(handler)
    port.started("voice")
    port.heard(Message(text="привіт", channel="voice", speech_ms=120.0))
    port.heard(Message(text="без часу", channel="voice"))
    port.nothing("voice")
    port.note("voice", "playing", "music")
    port.overheard("voice", "quiet")
    verdict = port.said_over("voice", "ага", 150.0, False)

    assert verdict is False
    assert seen == [
        ("/api/input", {"channel": "voice", "kind": "started"}),
        ("/api/input", {
            "channel": "voice", "kind": "heard", "text": "привіт",
            "addressed": True, "speech_ms": 120.0,
        }),
        ("/api/input", {
            "channel": "voice", "kind": "heard", "text": "без часу",
            "addressed": True,
        }),
        ("/api/input", {"channel": "voice", "kind": "nothing"}),
        ("/api/input", {
            "channel": "voice", "kind": "note", "key": "playing",
            "value": "music",
        }),
        ("/api/input", {
            "channel": "voice", "kind": "overheard", "text": "quiet",
        }),
        ("/api/input/said_over", {
            "channel": "voice", "text": "ага", "speech_ms": 150.0,
            "long": False,
        }),
    ]


def test_said_over_reads_the_brain_s_verdict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"turn": True})

    assert _port(handler).said_over("voice", "ага", 200.0, False) is True


def test_the_port_reports_what_the_controls_are_worth():
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    _port(handler).state({"mic": True, "volume": 0.5})

    assert seen == [("/api/input", {
        "channel": "voice", "kind": "state",
        "values": {"mic": True, "volume": 0.5},
    })]


def test_a_dropped_report_does_not_stop_the_body():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone")

    port = _port(handler)
    port.started("voice")
    assert port.said_over("voice", "ага", 200.0, False) is False


def test_the_microphone_survives_a_restart(tmp_path):
    path = tmp_path / "config.toml"
    settings = Settings(path=path)
    ears = Ears()
    ears.controls["speaker"] = True
    _Speaker(ears, settings=settings).feed(
        {"channel": "voice", "kind": "control", "name": "mic", "value": False})

    assert ears.controls["mic"] is False
    restored = Ears()
    restored.controls["speaker"] = True
    _apply_saved(restored, Settings(path=path))
    assert restored.controls["mic"] is False


def test_the_sound_switch_survives_a_restart(tmp_path):
    path = tmp_path / "config.toml"
    settings = Settings(path=path)
    ears = Ears()
    ears.controls["speaker"] = True
    _Speaker(ears, settings=settings).feed(
        {"channel": "voice", "kind": "control", "name": "speaker", "value": False})

    assert ears.controls["speaker"] is False
    restored = Ears()
    restored.controls["speaker"] = True
    _apply_saved(restored, Settings(path=path))
    assert restored.controls["speaker"] is False


def test_the_volume_survives_a_restart(tmp_path):
    path = tmp_path / "config.toml"
    settings = Settings(path=path)
    ears = Ears()
    ears.controls["speaker"] = True
    _Speaker(ears, settings=settings).feed(
        {"channel": "voice", "kind": "control", "name": "volume", "value": 0.35})

    assert ears.controls["volume"] == 0.35
    restored = Ears()
    restored.controls["speaker"] = True
    _apply_saved(restored, Settings(path=path))
    assert restored.controls["volume"] == 0.35


async def test_the_reply_stream_becomes_one_utterance_in_order():
    ears = Ears()
    await _follow(_lines([
        _event("reply", text="При"),
        _event("reply", text="віт."),
        ": keepalive",
        _event("said", text="Привіт."),
    ]), _Speaker(ears))

    await asyncio.wait_for(ears.finished.wait(), 1.0)
    assert ears.replies == [["При", "віт."]]


async def test_a_stray_piece_after_an_end_starts_a_new_reply():
    ears = Ears()
    speaker = _Speaker(ears)
    await _follow(_lines([
        _event("reply", text="перша"),
        _event("said", text="перша"),
        _event("reply", text="друга"),
        _event("said", text="друга"),
    ]), speaker)

    while len(ears.replies) < 2:
        await asyncio.sleep(0)
    assert ears.replies == [["перша"], ["друга"]]


async def test_the_mouth_controls_are_obeyed():
    ears = Ears()
    await _follow(_lines([
        _event("voice_paused"),
        _event("voice_resumed"),
        _event("voice_stopped"),
    ]), _Speaker(ears))

    assert (ears.paused, ears.resumed, ears.stopped) == (1, 1, 1)


async def test_a_new_reply_stops_what_is_still_sounding():
    ears = Ears()
    await _follow(_lines([
        _event("reply", text="перша"),
        _event("said", text="перша"),
        _event("reply", text="друга"),
    ]), _Speaker(ears))

    assert ears.stopped >= 1


async def test_the_body_says_when_the_mouth_is_sounding():
    ears = Ears()
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    port = _port(handler)
    await _follow(_lines([
        _event("reply", text="раз"),
        _event("said", text="раз"),
    ]), _Speaker(ears, port))
    await port.drain()
    while len(seen) < 2:
        await asyncio.sleep(0)

    sounds = [v["values"] for v in seen if v.get("kind") == "state"]
    assert sounds[0] == {"speaking": True}
    assert sounds[-1] == {"speaking": False}


async def test_a_control_from_the_page_is_applied_and_reported():
    ears = Ears()
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    port = _port(handler)
    await _follow(_lines([
        'data: {"kind": "control", "channel": "voice", "name": "volume", "value": 0.4}',
    ]), _Speaker(ears, port))
    await port.drain()

    assert ears.controls["volume"] == 0.4
    assert seen == [("/api/input", {
        "channel": "voice", "kind": "state",
        "values": {"mic": True, "volume": 0.4, "mode": "hearing"},
    })]


async def test_a_refused_control_still_reports_what_is_true():
    ears = Ears()
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    port = _port(handler)
    await _follow(_lines([
        'data: {"kind": "control", "channel": "voice", "name": "warp", "value": 9}',
    ]), _Speaker(ears, port))
    await port.drain()

    assert ears.controls == {"mic": True, "volume": 1.0}
    assert seen[-1] == ("/api/input", {
        "channel": "voice", "kind": "state",
        "values": {"mic": True, "volume": 1.0, "mode": "hearing"},
    })


async def test_stopping_ends_the_reply_in_flight():
    ears = Ears()
    await _follow(_lines([
        _event("reply", text="поча"),
        _event("voice_stopped"),
    ]), _Speaker(ears))

    await asyncio.wait_for(ears.finished.wait(), 1.0)
    assert ears.replies == [["поча"]]
    assert ears.stopped == 1


async def test_events_for_another_channel_never_reach_the_mouth():
    ears = Ears()
    await _follow(_lines([
        'data: {"kind": "reply", "channel": "text", "text": "x"}',
        'data: {"kind": "reply", "text": "y"}',
        'data: {"kind": "voice_paused", "channel": "screen"}',
    ]), _Speaker(ears))

    assert ears.replies == []
    assert ears.paused == 0


async def test_a_refused_registration_exits_rejected_with_the_reason(
    tmp_path, capsys,
):
    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=token,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/bodies"
        return httpx.Response(200, json={"approved": False, "why": "rejected"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    code = await run(settings, client=client)
    await client.aclose()

    assert code == part.EXIT_REJECTED
    assert "мозок не одобрив: rejected" in capsys.readouterr().err


def test_a_report_the_brain_never_answers_does_not_hold_the_voice():
    """25 October: the voice's loop reports synchronously; one call left
    waiting forever held it all, and the voice never knocked again."""
    import socket
    import time

    from heare import voice

    quiet = socket.socket()
    quiet.bind(("127.0.0.1", 0))
    quiet.listen(1)  # takes the connection, never answers
    try:
        port = HttpPort(httpx.Client(timeout=0.3), f"http://127.0.0.1:{quiet.getsockname()[1]}")
        began = time.monotonic()
        port.state({"mic": True})
        assert time.monotonic() - began < 2.0
    finally:
        quiet.close()
    assert voice.REPORT_TIMEOUT.read is not None and voice.REPORT_TIMEOUT.read <= 5.0


class _SpeakerDevice:
    mute_input = False
    mute_output = False
    mute_output_user = False

    def play(self, pcm: bytes) -> None:
        pass

    def stop_playback(self) -> int:
        return 0

    @property
    def playing(self) -> bool:
        return False


async def test_the_mic_stays_muted_until_the_last_live_utterance_closes():
    """Two replies can be live at once. The newer one closing first must
    not give the microphone back while the older one is still speaking."""
    speaker = _SpeakerDevice()
    mouth = Mouth(speaker)

    def synthesise(text: str):
        return None

    older = mouth.open(synthesise, mute_mic=True)
    newer = mouth.open(synthesise, mute_mic=True)
    assert speaker.mute_input is True

    newer.close()
    assert speaker.mute_input is True, "the older reply is still speaking"

    older.close()
    assert speaker.mute_input is False
