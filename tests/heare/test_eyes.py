"""The eyes body: the surroundings, posted to the brain's door."""

from __future__ import annotations

import json

import httpx

from heare.eyes import Door


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

    assert len(seen) == 1
    assert "could not tell the brain about app" in caplog.text
