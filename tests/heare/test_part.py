"""The wire every body uses: how a part knocks."""

from __future__ import annotations

import asyncio
import json

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


def _settings(tmp_path):
    from heare.config import Settings
    token = tmp_path / "token"
    token.write_text("master", encoding="utf-8")
    return Settings(path=tmp_path / "config.toml", api_token_file=token,
                    machine_token_file=tmp_path / "machine")


async def test_a_part_is_let_in_with_its_own_key_and_does_its_work(tmp_path):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "approved": True, "pid": "eyes@mac", "token": "own"})

    async def work(wire: part.Wire) -> int:
        assert wire.pid == "eyes@mac" and wire.channel == "eyes"
        await wire.post("/api/input", {"kind": "note"})
        return 7

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    code = await part.live({"channel": "eyes", "key": "eyes@mac"}, work,
                           settings=_settings(tmp_path), client=client)
    await client.aclose()

    assert code == 7
    knock, note = seen
    assert knock.headers["X-Heare-Token"] == "master"
    assert json.loads(knock.content)["v"] == part.PROTOCOL
    assert note.headers["X-Heare-Token"] == "own"


async def test_a_refused_part_does_no_work(tmp_path, capsys):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"approved": False, "why": "ні"})))

    async def work(wire):
        raise AssertionError("a refused part must not work")

    assert await part.live({"channel": "eyes"}, work,
                           settings=_settings(tmp_path), client=client) == 1
    assert "мозок не одобрив: ні" in capsys.readouterr().err
    await client.aclose()


async def test_a_call_outlives_a_brain_that_restarts(monkeypatch):
    monkeypatch.setattr(part, "RETRY_S", 0.0)
    answers = [httpx.RemoteProtocolError("dropped"), httpx.ConnectError("down"),
               httpx.Response(200, json={"id": 3})]

    def handler(request):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        wire = part.Wire(client, "http://brain", "opencode", "opencode@mac", "k")
        assert (await wire.post("/api/executor/claim")).json() == {"id": 3}


async def test_the_stream_is_read_with_the_key_and_only_this_channel_passes(monkeypatch):
    seen: list[httpx.Request] = []
    got: list[dict] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in (
            {"kind": "said", "channel": "voice"},
            {"kind": "said", "channel": "screen"}))
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(part.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    following = asyncio.create_task(part.follow("http://brain", "secret", "voice", got.append))
    while not got:
        await asyncio.sleep(0.01)
    following.cancel()
    await asyncio.gather(following, return_exceptions=True)

    assert seen[0].url.params["token"] == "secret"
    assert got == [{"kind": "said", "channel": "voice"}]
