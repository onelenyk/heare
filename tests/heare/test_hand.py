"""The hands' life (heare.hand) and the native hands: claiming, stopping, failing, the model."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from heare import hand, native_hands
from heare.native_hands import Chosen
from heare.talk import anthropic_turns


class Wire:
    """The brain's door as the part sees it: answers by path, calls recorded."""

    def __init__(self, answers: dict[str, list[httpx.Response]]) -> None:
        self.answers = answers
        self.posted: list[tuple[str, dict | None]] = []
        self.reports: list[dict] = []

    async def post(self, path: str, body: dict | None = None) -> httpx.Response:
        self.posted.append((path, body))
        for prefix, queue in self.answers.items():
            if path.startswith(prefix) and queue:
                return queue.pop(0)
        return httpx.Response(200, json={})

    def report(self, values: dict | None = None) -> None:
        self.reports.append(dict(values or {}))


class Hand:
    def __init__(self, does=None) -> None:
        self.does = does or (lambda task, desk: "Готово.")
        self.tasks: list[str] = []

    async def __call__(self, job, desk):
        self.tasks.append(job.request)
        return self.does(job.request, desk)


async def take(wire, work, **hooks):
    return await hand.take(wire, work, **hooks)


def _results(wire: Wire) -> list[dict]:
    return [body for path, body in wire.posted if "/result" in path]


async def test_no_work_is_not_a_loop_of_requests():
    wire = Wire({"/api/executor/claim": [httpx.Response(200, json={})]})
    assert await take(wire, Hand()) is False
    assert await take(Wire({"/api/executor/claim": [httpx.Response(403, text="no")]}),
                      Hand()) is False
    assert hand.IDLE_S >= 1.0


async def test_work_is_done_and_its_result_sent():
    wire = Wire({"/api/executor/claim": [httpx.Response(200, json={"id": 4, "request": "блимни"})]})
    hand = Hand()

    assert await take(wire, hand) is True

    assert hand.tasks == ["блимни"]
    assert _results(wire) == [{"status": "done", "result": "Готово."}]


async def test_a_failing_model_is_a_sentence_not_a_traceback():
    def broken(task, desk):
        raise httpx.HTTPStatusError("401 Unauthorized for url https://x/?key=secret",
                                    request=httpx.Request("POST", "https://x"),
                                    response=httpx.Response(401))

    wire = Wire({"/api/executor/claim": [httpx.Response(200, json={"id": 1, "request": "x"})]})
    await take(wire, Hand(broken), failed=lambda exc: native_hands.FAILED)

    assert _results(wire) == [{"status": "failed", "result": native_hands.FAILED}]


async def test_a_stop_is_heard_through_the_step_and_nothing_more_is_done():
    wire = Wire({"/api/executor/7/step": [httpx.Response(200, json={"stopped": True})]})
    desk = hand.Desk(wire, hand.Job(7, "x"))

    desk.step("думаю")
    await asyncio.gather(*desk._steps)

    assert desk.stopped is True


async def test_a_step_after_a_brain_restart_stops_the_hand():
    wire = Wire({"/api/executor/7/step": [httpx.Response(404, text="no such job")]})
    desk = hand.Desk(wire, hand.Job(7, "x"))

    desk.step("думаю")
    await asyncio.gather(*desk._steps)

    assert desk.stopped is True

    # A question for a job the brain has forgotten is a stop, not a failure
    # that keeps the hand working and then drops its result.
    wire = Wire({
        "/api/executor/claim": [httpx.Response(200, json={"id": 8, "request": "x"})],
        "/api/executor/8/ask": [httpx.Response(404, text="no such job")],
        "/api/executor/8/result": [httpx.Response(404, text="no such job")],
    })

    async def asks(job, desk):
        await desk.ask("що робити?")
        return "не мало статися"

    assert await take(wire, asks) is True
    assert _results(wire) == [{"status": "stopped", "result": "Stopped."}]


async def test_a_refused_result_is_logged_not_swallowed(caplog):
    wire = Wire({
        "/api/executor/claim": [httpx.Response(200, json={"id": 9, "request": "x"})],
        "/api/executor/9/result": [httpx.Response(404, text="no such job")],
    })

    with caplog.at_level(logging.WARNING, logger="heare.hand"):
        assert await take(wire, Hand()) is True

    assert any("refused the result for job 9" in record.message
               for record in caplog.records)


class _Settings:
    llm_provider = "deepseek"
    deepseek_api_key = "sk-test"
    deepseek_model = ""
    deepseek_base_url = ""

    def __getattr__(self, name):
        return ""


async def test_the_model_is_asked_with_tools_and_its_calls_come_back():
    seen: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "auth": request.headers["authorization"],
                     "body": json.loads(request.content)})
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Блимаю.",
            "tool_calls": [{"id": "c1", "function": {"name": "light",
                                                      "arguments": '{"mode": "blink"}'}}],
        }}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        chat = Chosen(_Settings(), client)
        events = [e async for e in chat.reply([{"role": "user", "content": "x"}],
                                              [{"type": "function", "function": {"name": "light"}}])]

    assert seen[0]["url"].endswith("/chat/completions")
    assert seen[0]["auth"] == "Bearer sk-test"
    assert seen[0]["body"]["tools"][0]["function"]["name"] == "light"
    assert events == [{"type": "delta", "text": "Блимаю."},
                      {"type": "tool_call", "id": "c1", "name": "light",
                       "arguments": '{"mode": "blink"}'}]


async def test_a_stop_from_the_brain_is_a_result_but_a_shutdown_goes_on_out():
    async def stopped(job, desk):
        raise asyncio.CancelledError  # what the desk raises on a stop

    wire = Wire({"/api/executor/claim": [httpx.Response(200, json={"id": 2, "request": "x"})]})
    assert await take(wire, stopped) is True
    assert _results(wire) == [{"status": "stopped", "result": "Stopped."}]

    async def forever(job, desk):
        await asyncio.Event().wait()

    wire = Wire({"/api/executor/claim": [httpx.Response(200, json={"id": 3, "request": "x"})]})
    running = asyncio.create_task(take(wire, forever))
    await asyncio.sleep(0.01)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert _results(wire) == []


class _Anthropic(_Settings):
    llm_provider = "zai"
    deepseek_api_key = ""
    zai_api_key = "z-test"


async def test_an_anthropic_provider_is_spoken_to_in_its_own_shape():
    seen: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "key": request.headers.get("x-api-key"),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": "Блимаю."},
            {"type": "tool_use", "id": "t1", "name": "light", "input": {"mode": "blink"}}]})

    messages = [{"role": "system", "content": "будь коротким"},
                {"role": "user", "content": "блимни"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c0", "type": "function",
                     "function": {"name": "light", "arguments": '{"mode": "on"}'}}]},
                {"role": "tool", "tool_call_id": "c0", "content": "Світло: on."}]
    tools = [{"type": "function", "function": {"name": "light", "description": "d",
                                                "parameters": {"type": "object"}}}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        events = [e async for e in Chosen(_Anthropic(), client).reply(messages, tools)]

    body = seen[0]["body"]
    assert seen[0]["url"].endswith("/v1/messages") and seen[0]["key"] == "z-test"
    assert body["system"] == "будь коротким"
    assert body["tools"] == [{"name": "light", "description": "d",
                              "input_schema": {"type": "object"}}]
    assert [t["role"] for t in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][0]["input"] == {"mode": "on"}
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert events[1] == {"type": "tool_call", "id": "t1", "name": "light",
                         "arguments": '{"mode": "blink"}'}


def test_turns_of_one_role_are_joined_for_anthropic():
    system, turns = anthropic_turns([{"role": "user", "content": "a"},
                                     {"role": "user", "content": "b"}])
    assert system == "" and len(turns) == 1 and len(turns[0]["content"]) == 2


def _part(name: str, *own: dict) -> dict:
    return {"channel": f"oled-{name}", "name": name, "about": "",
            "skills": list(own), "values": {}}


class _Devices:
    def __init__(self, parts: dict[str, dict], answer: dict | None = None) -> None:
        self.parts = parts
        self.calls: list[tuple[str, str, dict]] = []
        self.answer = answer or {"ok": True, "result": "зроблено"}

    async def skilled(self) -> dict[str, dict]:
        return self.parts

    async def call(self, pid: str, name: str, args: dict) -> dict:
        self.calls.append((pid, name, args))
        return self.answer


class _Desk:
    stopped = False

    def __init__(self, allow: bool = True, says: str = "") -> None:
        self.allow, self.says = allow, says
        self.permits: list[str] = []
        self.questions: list[str] = []

    def step(self, text: str) -> None:
        pass

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        self.permits.append(kind)
        return self.allow

    async def ask(self, text: str) -> str:
        self.questions.append(text)
        return self.says


def _native(devices: _Devices, *answers):
    from heare.native_hands import NativeHand
    from tests.heare.fakes import Script

    model = Script()
    model.will_say(*answers)
    return NativeHand(model, devices), model


async def test_the_native_forced_final_turn_is_inside_the_budget(monkeypatch):
    from heare import native_hands

    class Slow:
        async def reply(self, messages, verbs):
            await asyncio.sleep(10)
            yield {"type": "delta", "text": "пізно"}

    monkeypatch.setattr(native_hands, "STEPS", 0)
    monkeypatch.setattr(native_hands, "DEADLINE_S", 0.05)
    hand = native_hands.NativeHand(Slow(), _Devices({}))
    started = asyncio.get_running_loop().time()

    assert await hand.run("покажи", _Desk()) == "Done: "
    assert asyncio.get_running_loop().time() - started < 1.0


async def test_duplicate_skill_names_use_the_effect_of_the_part_called():
    from tests.heare.fakes import Says

    harmless = {"name": "show", "about": "Показати слова", "effect": "harmless",
                "params": {"type": "object", "properties": {"text": {"type": "string"}}}}
    changing = {**harmless, "effect": "change"}
    parts = {"oled-a": _part("desk", harmless), "oled-b": _part("desk", changing)}
    devices = _Devices(parts)
    call = ("desk__show", {"text": "hi"})

    hand, _ = _native(devices, Says(calls=(call,)), "Показав.")
    quiet = _Desk(says="1")
    await hand.run("покажи", quiet)
    assert quiet.permits == []
    assert devices.calls == [("oled-a", "show", {"text": "hi"})]

    devices.calls.clear()
    hand, _ = _native(devices, Says(calls=(call,)), "Не можна.")
    guarded = _Desk(allow=False, says="2")
    await hand.run("покажи", guarded)
    assert guarded.permits == ["judge"]
    assert devices.calls == []
