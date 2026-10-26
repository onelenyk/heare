from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from heare.api import server as api_server
from heare.api.server import Screen, build
from heare.config import Settings
from heare.core import Permission, Question
from tests.heare.fakes import open_room

# The words that must never reach a filtered stream.
WORDS = {"text", "what", "why", "detail", "arguments", "answer"}


async def _open(tmp_path):
    room = await open_room()
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts",
        part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json",
        memory_dir=tmp_path / "memory", heard_dir=tmp_path / "heard",
        backups_dir=tmp_path / "home" / "backups",
    )
    room.core.add_output(Screen(room.core))
    client = TestClient(TestServer(build(room.core, settings)))
    await client.start_server()
    master = {"X-Heare-Token": (tmp_path / "token").read_text()}
    return room, client, master


async def _listening(client, master, knock: dict) -> dict:
    asked = asyncio.create_task(client.post("/api/bodies", headers=master, json=knock))
    await asyncio.sleep(0.05)
    await client.post(f"/api/bodies/{knock['key']}/approve", headers=master)
    return {"X-Heare-Token": (await (await asked).json())["token"]}


async def _payloads(stream, *, want: int | None = None, wait: float = 0.4) -> list[dict]:
    got: list[dict] = []
    end = asyncio.get_running_loop().time() + wait
    while want is None or len(got) < want:
        left = end - asyncio.get_running_loop().time()
        if left <= 0:
            break
        try:
            chunk = await asyncio.wait_for(stream.content.readany(), left)
        except TimeoutError:
            break
        for line in chunk.decode().splitlines():
            if line.startswith("data: "):
                got.append(json.loads(line[6:]))
    return got


async def _close(room, client):
    await client.close()
    await room.close()


async def test_a_status_stream_carries_only_the_kinds_it_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, master = await _open(tmp_path)
    own = await _listening(client, master, {
        "channel": "panel", "forms": ["output"], "key": "panel@mac",
        "hears": "all"})
    stream = await client.get(
        "/api/events?kinds=phase,said,permission,cannot", headers=own)

    room.core.state.emit("heard", text="secret words", channel="voice")
    room.core.state.emit("reply", text="secret reply", channel="voice")
    room.core.state.emit("said", text="secret words", channel="voice", failed=None)
    room.core.state.emit(
        "permission", **Permission(7, 1, "hands", "sudo rm", "because").view())
    room.core.state.emit("cannot", what="secret verb", why="secret reason")

    got = await _payloads(stream, want=4)
    assert [p["kind"] for p in got] == ["phase", "said", "permission", "cannot"]
    assert all(not (WORDS & payload.keys()) for payload in got)

    said = next(p for p in got if p["kind"] == "said")
    assert set(said) == {"kind", "at", "channel", "failed"}
    assert said["channel"] == "voice" and said["failed"] is None

    permission = next(p for p in got if p["kind"] == "permission")
    assert set(permission) == {"kind", "at", "id"} and permission["id"] == 7

    cannot = next(p for p in got if p["kind"] == "cannot")
    assert set(cannot) == {"kind", "at"}

    stream.close()
    await _close(room, client)


async def test_a_status_stream_opens_with_the_present(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, master = await _open(tmp_path)
    room.core.state.set_phase("thinking")
    room.core.state.permission = Permission(7, 1, "hands", "sudo rm", "because")
    room.core.state.question = Question(9, 1, "hands", "which file?")
    own = await _listening(client, master, {
        "channel": "panel", "forms": ["output"], "key": "panel@mac",
        "hears": "all"})
    stream = await client.get(
        "/api/events?kinds=phase,permission,question", headers=own)

    got = await _payloads(stream, want=3)
    assert [p["kind"] for p in got[:3]] == ["phase", "permission", "question"]
    assert got[0]["phase"] == "thinking"
    assert set(got[1]) == {"kind", "at", "id"} and got[1]["id"] == 7
    assert set(got[2]) == {"kind", "at", "id"} and got[2]["id"] == 9
    assert all(not (WORDS & payload.keys()) for payload in got)

    stream.close()
    await _close(room, client)


async def test_replay_is_projected_too(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, master = await _open(tmp_path)
    room.core.state.emit("heard", text="secret heard", channel="voice")
    room.core.state.emit("said", text="secret reply", channel="voice", failed=None)
    own = await _listening(client, master, {
        "channel": "panel", "forms": ["output"], "key": "panel@mac",
        "hears": "all"})
    stream = await client.get("/api/events?kinds=said&replay=10", headers=own)

    got = await _payloads(stream, want=1)
    assert [p["kind"] for p in got] == ["said"]
    assert set(got[0]) == {"kind", "at", "channel", "failed"}
    assert "text" not in got[0]

    stream.close()
    await _close(room, client)


async def test_an_unknown_or_text_bearing_kind_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, master = await _open(tmp_path)
    own = await _listening(client, master, {
        "channel": "panel", "forms": ["output"], "key": "panel@mac",
        "hears": "all"})
    stream = await client.get(
        "/api/events?kinds=heard,reply,nope", headers=own)
    assert await _payloads(stream, wait=0.2) == []

    room.core.state.emit("heard", text="secret heard", channel="voice")
    room.core.state.emit("reply", text="secret reply", channel="voice")
    assert await _payloads(stream, wait=0.2) == []

    stream.close()
    await _close(room, client)


async def test_an_unfiltered_stream_still_carries_the_words(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, master = await _open(tmp_path)
    stream = await client.get("/api/events", headers=master)

    room.core.state.emit("heard", text="secret heard", channel="voice")
    room.core.state.emit("said", text="secret words", channel="voice", failed=None)

    got = await _payloads(stream, want=2)
    assert [p["kind"] for p in got] == ["heard", "said"]
    assert got[0]["text"] == "secret heard"
    assert got[1]["text"] == "secret words"
    assert "phase" not in {p["kind"] for p in got}

    stream.close()
    await _close(room, client)
