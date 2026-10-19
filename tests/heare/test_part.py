"""The wire every body uses: how a part knocks."""

from __future__ import annotations

import asyncio

import httpx

from heare import part


def _brain(answers: list):
    """A brain that answers each knock with the next of *answers*."""
    async def handler(request: httpx.Request) -> httpx.Response:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, float):
            await asyncio.sleep(answer)
            return httpx.Response(200, json={"approved": True})
        return httpx.Response(200, json=answer)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_brain_not_up_yet_is_waited_for(monkeypatch, capsys):
    monkeypatch.setattr(part, "RETRY_S", 0.0)
    down = httpx.ConnectError("refused")
    async with _brain([down, down, {"approved": True}]) as client:
        answer = await part.knock(client, "http://brain", {"channel": "eyes"})
    assert answer == {"approved": True}
    err = capsys.readouterr().err
    assert err.count("мозок ще не відповідає") == 1


async def test_a_trusted_part_knocks_in_silence(capsys):
    async with _brain([{"approved": True}]) as client:
        await part.knock(client, "http://brain", {"channel": "eyes"})
    assert capsys.readouterr().err == ""


async def test_a_slow_answer_says_it_waits_for_the_person(monkeypatch, capsys):
    monkeypatch.setattr(part, "ASKING_S", 0.01)
    async with _brain([0.05]) as client:
        await part.knock(client, "http://brain", {"channel": "eyes"})
    assert "чекаю, доки мене одобрять" in capsys.readouterr().err


def test_a_sync_client_knocks_too():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"approved": True})))
    assert asyncio.run(part.knock(client, "http://brain", {})) == {"approved": True}
