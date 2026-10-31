"""The eyes body: the surroundings, posted to the brain's door."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys

import httpx
import pytest

from heare.config import Settings
from heare.eyes import Door
from heare.surroundings import sense
from heare.surroundings.watch import Surroundings


def _answered(seen: list[httpx.Request], status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"ok": status == 200})

    return httpx.MockTransport(handler)


async def test_a_note_is_posted_as_the_door_expects():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(
        transport=_answered(seen), headers={"X-Heare-Token": "secret"},
    ) as client:
        door = Door(client, "http://127.0.0.1:9790")
        door.note("playing", ["Music"])
        await door.settle()

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/api/input"
    assert json.loads(request.content) == {
        "channel": "eyes", "kind": "note", "key": "playing", "value": ["Music"],
    }
    assert request.headers["X-Heare-Token"] == "secret"


async def test_a_failed_post_is_logged_and_does_not_kill_the_loop(caplog):
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_answered(seen, status=500)) as client:
        door = Door(client, "http://127.0.0.1:9790")
        with caplog.at_level("WARNING", logger="heare.eyes"):
            door.note("app", "Cursor")
            await door.settle()

    assert [json.loads(r.content)["kind"] for r in seen] == ["note", "state"]
    assert "could not tell the brain about app" in caplog.text


async def test_the_label_follows_the_front_app():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_answered(seen)) as client:
        door = Door(client, "http://127.0.0.1:9790")
        door.note("app", "Telegram")
        await door.settle()

    assert door.values()["sees"] == "Telegram"
    assert json.loads(seen[-1].content) == {
        "channel": "eyes", "kind": "state",
        "values": {"looking": True, "sees": "Telegram"},
    }


async def test_looking_survives_a_restart(tmp_path):
    path = tmp_path / "config.toml"
    async with httpx.AsyncClient(transport=_answered([])) as client:
        door = Door(client, "http://127.0.0.1:9790", Settings(path=path))
        door.event({"kind": "control", "channel": "eyes",
                    "name": "looking", "value": False})
        await door.settle()
        restored = Door(client, "http://127.0.0.1:9790", Settings(path=path))

    assert door.looking is False
    assert restored.looking is False


async def test_looking_can_be_turned_off_and_is_reported():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=_answered(seen)) as client:
        door = Door(client, "http://127.0.0.1:9790")
        door.event({"kind": "control", "channel": "eyes",
                    "name": "looking", "value": False})
        await door.settle()
        door.note("app", "Cursor")
        await door.settle()

    assert door.looking is False
    assert len(seen) == 1
    assert json.loads(seen[0].content) == {
        "channel": "eyes", "kind": "state",
        "values": {"looking": False, "sees": "quiet"},
    }


def test_front_app_names_the_frontmost_app():
    """The shipped eyes wheel must carry the AppKit bridge, not swallow it."""
    if sys.platform != "darwin":
        pytest.skip("AppKit exists only on macOS")
    assert importlib.util.find_spec("AppKit") is not None
    name = sense.front_app()
    assert name is None or name != ""


async def test_a_lost_note_is_sent_again_after_the_brain_returns(monkeypatch):
    notes: list[dict] = []
    attempts = 0
    delivered = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        body = json.loads(request.content)
        if body.get("kind") == "note" and body.get("key") == "app":
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("brain restarting", request=request)
        if body.get("kind") == "note":
            notes.append(body)
            if body.get("key") == "app":
                delivered.set()
        return httpx.Response(200, json={"ok": True})

    import heare.surroundings.watch as module

    was = module.EVERY_S
    module.EVERY_S = 0.01
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            door = Door(client, "http://127.0.0.1:9790")
            watch = Surroundings(playing=lambda: [], front=lambda: "Safari",
                                 idle=lambda: 1.0)
            run = asyncio.create_task(watch.run(door))
            await asyncio.wait_for(delivered.wait(), 2.0)
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            await door.settle()
    finally:
        module.EVERY_S = was

    apps = [note for note in notes if note["key"] == "app"]
    assert attempts >= 2
    assert {note["value"] for note in apps} == {"Safari"}
    assert door.sees == "Safari"


async def test_a_failed_sensor_says_unknown(caplog):
    watch = Surroundings(playing=lambda: None, front=lambda: None,
                         idle=lambda: None)
    with caplog.at_level("WARNING", logger="heare.surroundings"):
        assert await watch._facts() == {
            "playing": None, "app": None, "present": None,
        }
        told: list[tuple[str, object]] = []

        class Port:
            def note(self, key: str, value: object) -> None:
                told.append((key, value))

        import heare.surroundings.watch as module

        was = module.EVERY_S
        module.EVERY_S = 0.01
        try:
            run = asyncio.create_task(watch.run(Port()))
            await asyncio.sleep(0.03)
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        finally:
            module.EVERY_S = was

    assert told == []
    assert "unknown" in caplog.text
