"""The voice body: its port's payloads, its stream, and its refusal."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from heare.config import Settings
from heare.core import Message
from heare.voice import HttpPort, _follow, _Speaker, run


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
    port.note("playing", "music")
    port.overheard("voice", "тихо")
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
            "channel": "voice", "kind": "overheard", "text": "тихо",
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

    await _follow(_lines([
        _event("reply", text="раз"),
        _event("said", text="раз"),
    ]), _Speaker(ears, _port(handler)))

    for _ in range(100):
        if any(v.get("values", {}).get("speaking") is False for v in seen):
            break
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

    await _follow(_lines([
        'data: {"kind": "control", "channel": "voice", "name": "volume", "value": 0.4}',
    ]), _Speaker(ears, _port(handler)))

    assert ears.controls["volume"] == 0.4
    assert seen == [("/api/input", {
        "channel": "voice", "kind": "state",
        "values": {"mic": True, "volume": 0.4, "mode": "чує"},
    })]


async def test_a_refused_control_still_reports_what_is_true():
    ears = Ears()
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    await _follow(_lines([
        'data: {"kind": "control", "channel": "voice", "name": "warp", "value": 9}',
    ]), _Speaker(ears, _port(handler)))

    assert ears.controls == {"mic": True, "volume": 1.0}
    assert seen[-1] == ("/api/input", {
        "channel": "voice", "kind": "state",
        "values": {"mic": True, "volume": 1.0, "mode": "чує"},
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


async def test_a_refused_registration_exits_one_with_the_reason(
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

    assert code == 1
    assert "мозок не одобрив: rejected" in capsys.readouterr().err
