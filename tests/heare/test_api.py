"""The API, driven the way a screen drives it."""

from __future__ import annotations

import asyncio
import json
import socket
from importlib import metadata
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from heare import pairing
from heare.api import auth as authlib, pair
from heare.api import registry as registry_mod
from heare.api import server as api_server
from heare.api.server import Screen, VoiceOut, build
from heare.config import Settings
from heare.model.providers import PROVIDERS
from tests.heare.fakes import Says, Worker, open_room


async def _open(tmp_path, *, facts=None, intents=None, memory_dir=None,
                features=None, journal=None, memory=None, **room_kw):
    room = await open_room(**room_kw)
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts",
        part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json",
        memory_dir=memory_dir if memory_dir is not None else tmp_path / "memory",
        heard_dir=tmp_path / "heard",
    )
    room.core.add_output(Screen(room.core))
    mind = None
    if facts is not None and intents is not None:
        from heare.mind import Mind
        mind = Mind(facts, intents, settings, journal=journal, memory=memory)
    client = TestClient(TestServer(
        build(room.core, settings, mind=mind, features=features)))
    await client.start_server()
    token = (tmp_path / "token").read_text()
    return room, client, {"X-Heare-Token": token}, settings


async def _close(room, client):
    await client.close()
    await room.close()


async def test_nothing_answers_without_the_token(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    assert (await client.get("/api/state")).status == 401
    assert (await client.post("/api/message", json={"text": "привіт"})).status == 401
    assert (await client.get("/api/state", headers=auth)).status == 200
    await _close(room, client)


async def test_a_typed_message_is_answered_as_events(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    events = room.core.state.subscribe()
    room.model.will_say("Чотири.")
    response = await client.post("/api/message", json={"text": "Скільки буде 2+2?"}, headers=auth)
    assert response.status == 200
    await room.settled()
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    assert "".join(e.data["text"] for e in seen if e.kind == "reply") == "Чотири. "
    assert [e.data["channel"] for e in seen if e.kind == "said"] == ["screen"]
    assert room.page.said == []  # answered where it was asked, not elsewhere
    await _close(room, client)


async def test_a_voice_body_talks_to_the_core_over_the_door(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    room.model.will_say("Привіт.")
    started = await client.post(
        "/api/input", json={"kind": "started", "channel": "voice"}, headers=auth)
    heard = await client.post(
        "/api/input", json={"kind": "heard", "channel": "voice", "text": "привіт"},
        headers=auth)
    assert started.status == 200 and heard.status == 200
    await room.settled()
    assert any("привіт" in str(m) for m in room.model.asked)
    await _close(room, client)


async def test_a_note_updates_the_surroundings_and_is_announced(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    events = room.core.state.subscribe()
    answered = await client.post("/api/input", json={
        "kind": "note", "channel": "eyes", "key": "playing", "value": ["Music"],
    }, headers=auth)
    assert answered.status == 200
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["around"]["playing"] == ["Music"]
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    around = [e for e in seen if e.kind == "around"]
    assert around[-1].data == {"key": "playing", "value": ["Music"]}
    await _close(room, client)


async def test_a_voice_over_the_reply_gets_a_verdict(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    response = await client.post("/api/input/said_over", json={
        "channel": "voice", "text": "ага", "speech_ms": 200.0, "long": False,
    }, headers=auth)
    assert response.status == 200
    assert isinstance((await response.json())["turn"], bool)
    await _close(room, client)


async def test_the_voice_output_streams_the_reply_as_pieces(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    room.core.add_output(VoiceOut(room.core))
    events = room.core.state.subscribe()
    room.model.will_say("Привіт.")
    await client.post("/api/input", json={"kind": "heard", "channel": "voice",
                                          "text": "привіт"}, headers=auth)
    await room.settled()
    channels = []
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "reply":
            channels.append(event.data["channel"])
    assert channels and set(channels) == {"voice"}  # the reply went to the voice
    await _close(room, client)


async def test_the_event_stream_carries_what_happens(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    stream = await client.get("/api/events", headers=auth)
    room.model.will_say("Привіт.")
    await client.post("/api/message", json={"text": "Привіт"}, headers=auth)
    kinds = []
    while "said" not in kinds:
        raw = await asyncio.wait_for(stream.content.readline(), 3.0)
        if raw.startswith(b"data: "):
            kinds.append(json.loads(raw[6:])["kind"])
    assert kinds.index("heard") < kinds.index("said")
    stream.close()
    await _close(room, client)


async def test_a_task_is_started_seen_and_stopped(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    assert (await client.post("/api/tasks", json={"request": "зроби"}, headers=auth)).status == 409
    worker = Worker()
    room.core.add_executor(worker)
    started = await (await client.post("/api/tasks", json={"request": "зроби"}, headers=auth)).json()
    await room.until(lambda: worker.tasks)
    listed = await (await client.get("/api/tasks", headers=auth)).json()
    assert [t["status"] for t in listed] == ["running"]
    stopped = await (await client.post(f"/api/tasks/{started['id']}/stop", headers=auth)).json()
    assert stopped == {"stopped": True}
    await room.until(lambda: worker.stopped)
    await _close(room, client)


async def test_a_button_answers_a_permission(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    worker = Worker(asks=(("записати файл", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    state = await (await client.get("/api/state", headers=auth)).json()
    asked = state["permission"]
    assert asked["what"] == "записати файл"
    await client.post(f"/api/permission/{asked['id']}", json={"allow": True}, headers=auth)
    await room.until(lambda: worker.allowed)
    assert worker.allowed == [True]
    await _close(room, client)


async def test_a_key_can_be_saved_and_never_read(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    room, client, auth, _ = await _open(tmp_path)
    before = await (await client.get("/api/keys", headers=auth)).json()
    assert before["DEEPSEEK_API_KEY"] == {"source": None, "env": False, "stored": False}
    saved = await client.post(
        "/api/keys", json={"name": "DEEPSEEK_API_KEY", "value": "sk-test-value"}, headers=auth
    )
    assert saved.status == 200
    after = await (await client.get("/api/keys", headers=auth)).json()
    assert after["DEEPSEEK_API_KEY"] == {"source": "stored", "env": False, "stored": True}
    text = await (await client.get("/api/keys", headers=auth)).text()
    assert "sk-test-value" not in text
    for path in ("/api/settings", "/api/state"):
        assert "sk-test-value" not in await (await client.get(path, headers=auth)).text()
    refused = await client.post("/api/keys", json={"name": "PATH", "value": "x"}, headers=auth)
    assert refused.status == 400
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    await _close(room, client)


async def test_a_key_in_the_environment_is_never_read_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test-value")
    room, client, auth, _ = await _open(tmp_path)
    response = await client.get("/api/keys", headers=auth)
    text = await response.text()
    assert "sk-test-value" not in text
    assert json.loads(text)["GROQ_API_KEY"] == {
        "source": "env", "env": True, "stored": False,
    }
    await _close(room, client)


async def test_a_key_in_the_environment_can_be_adopted(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-test-value")
    room, client, auth, _ = await _open(tmp_path)
    events = room.core.state.subscribe()
    saved = await client.post(
        "/api/keys/adopt", json={"name": "GROQ_API_KEY"}, headers=auth)
    assert saved.status == 200
    assert await saved.json() == {"saved": "GROQ_API_KEY"}
    report = await (await client.get("/api/keys", headers=auth)).json()
    assert report["GROQ_API_KEY"] == {"source": "env", "env": True, "stored": True}
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    assert any(e.kind == "key" and e.data == {"name": "GROQ_API_KEY"} for e in seen)
    monkeypatch.delenv("GROQ_API_KEY")
    report = await (await client.get("/api/keys", headers=auth)).json()
    assert report["GROQ_API_KEY"] == {"source": "stored", "env": False, "stored": True}
    await _close(room, client)


async def test_adopting_refuses_an_unknown_key_or_an_empty_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    room, client, auth, _ = await _open(tmp_path)
    unknown = await client.post(
        "/api/keys/adopt", json={"name": "PATH"}, headers=auth)
    assert unknown.status == 400
    missing = await client.post(
        "/api/keys/adopt", json={"name": "ZAI_API_KEY"}, headers=auth)
    assert missing.status == 400
    monkeypatch.setenv("ZAI_API_KEY", "   ")
    blank = await client.post(
        "/api/keys/adopt", json={"name": "ZAI_API_KEY"}, headers=auth)
    assert blank.status == 400
    await _close(room, client)


async def test_a_setting_changed_is_read_back_and_a_key_is_not_a_setting(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    (tmp_path / "config.toml").write_text('llm_provider = "opencode"\n[table]\nx = 1\n')
    changed = await client.post(
        "/api/settings", json={"llm_provider": "deepseek", "deepseek_model": "deepseek-chat"},
        headers=auth,
    )
    assert (await changed.json())["llm_provider"] == "deepseek"
    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_model == "deepseek-chat"
    assert "[table]" in (tmp_path / "config.toml").read_text()
    refused = await client.post("/api/settings", json={"deepseek_api_key": "x"}, headers=auth)
    assert refused.status == 400
    await _close(room, client)


async def test_models_are_read_live_when_the_provider_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-secret")
    seen: dict = {}

    async def fake_models(config, api_key, timeout=10.0):
        seen["key"] = api_key
        return ["deepseek-v4-flash", "deepseek-v4-pro", "extra-model"]

    monkeypatch.setattr(api_server, "list_models", fake_models)
    room, client, auth, _ = await _open(tmp_path)
    response = await client.get(
        "/api/models", params={"provider": "deepseek"}, headers=auth)
    assert await response.json() == {
        "provider": "deepseek",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "extra-model"],
        "source": "live",
        "default": "deepseek-v4-flash",
    }
    assert seen["key"] == "sk-live-secret"
    text = await (await client.get(
        "/api/models", params={"provider": "deepseek"}, headers=auth)).text()
    assert "sk-live-secret" not in text
    await _close(room, client)


async def test_models_fall_back_to_the_whitelist_when_the_provider_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-broken")

    async def broken(config, api_key, timeout=10.0):
        raise RuntimeError("provider down")

    monkeypatch.setattr(api_server, "list_models", broken)
    room, client, auth, _ = await _open(tmp_path)
    body = await (await client.get(
        "/api/models", params={"provider": "deepseek"}, headers=auth)).json()
    assert body["source"] == "whitelist"
    assert body["models"] == list(PROVIDERS["deepseek"].model_whitelist)
    assert body["default"] == PROVIDERS["deepseek"].default_model
    await _close(room, client)


async def test_models_without_a_key_use_the_whitelist_and_do_not_call_out(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    called = False

    async def fake_models(config, api_key, timeout=10.0):
        nonlocal called
        called = True
        return ["should-not-be-used"]

    monkeypatch.setattr(api_server, "list_models", fake_models)
    room, client, auth, _ = await _open(tmp_path)
    body = await (await client.get(
        "/api/models", params={"provider": "deepseek"}, headers=auth)).json()
    assert body["source"] == "whitelist"
    assert body["models"] == list(PROVIDERS["deepseek"].model_whitelist)
    assert called is False
    await _close(room, client)


async def test_models_refuse_an_unknown_provider(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    refused = await client.get(
        "/api/models", params={"provider": "nope"}, headers=auth)
    assert refused.status == 400
    assert "unknown provider" in await refused.text()
    await _close(room, client)


async def test_memory_is_searchable(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    found = await (await client.get("/api/memory", params={"q": "відпустка"}, headers=auth)).json()
    assert found == {"said": "Ти казав про це вчора."}
    await _close(room, client)


async def test_a_screen_is_saved_listed_and_served_as_a_client(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    saved = await client.put("/api/screens/costs", json={"html": "<h1>Витрати</h1>"}, headers=auth)
    assert saved.status == 200
    assert await (await client.get("/api/screens", headers=auth)).json() == ["costs"]
    assert (await client.get("/screens/costs")).status == 401
    shown = await (await client.get("/screens/costs", headers=auth)).text()
    assert "<h1>Витрати</h1>" in shown and "HEARE_TOKEN" not in shown
    bad = await client.put("/api/screens/..%2Fevil", json={"html": "x"}, headers=auth)
    assert bad.status in (400, 404)
    assert not (tmp_path / "evil.html").exists()
    await _close(room, client)


async def test_the_built_in_page_is_served(tmp_path):
    room, client, _, _ = await _open(tmp_path)
    page = await (await client.get("/")).text()
    assert "HEARE_TOKEN" not in page and "Керування" in page
    assert 'id="pairing"' in page  # a browser without a key asks for a code
    assert "Пристрої" in page and "Підключити пристрій" in page
    await _close(room, client)


async def test_a_delegated_reply_can_be_watched_from_the_screen(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    worker = Worker(result="Білд зелений.")
    room.core.add_executor(worker)
    room.model.will_say(Says(calls=(("delegate", {"task": "глянь білд"}),)), "Білд пройшов.")
    await client.post("/api/message", json={"text": "Глянь білд"}, headers=auth)
    await room.until(lambda: worker.tasks)
    worker.release.set()
    await room.until(lambda: len(room.model.asked) == 2)
    await room.settled()
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["tasks"][0]["status"] == "done"
    await _close(room, client)


async def test_a_screen_asked_for_is_written_saved_and_announced(tmp_path):
    from heare.api.screens import ScreenMaker

    room, client, auth, settings = await _open(tmp_path)

    async def write(system: str, request: str) -> str:
        assert "/api/state" in system  # the model is told what it may call
        return "name: my-tasks\n<!doctype html><h1>Справи</h1>"

    room.core.add_executor(
        ScreenMaker(settings.screens_dir, write, room.core.state.emit)
    )
    room.model.will_say("Екран готовий.")
    task = room.core.delegate("зроби екран зі справами")
    await room.until(lambda: task.status == "done")
    assert "my-tasks" in task.result
    assert await (await client.get("/api/screens", headers=auth)).json() == ["my-tasks"]
    assert "<h1>Справи</h1>" in await (await client.get("/screens/my-tasks", headers=auth)).text()
    await _close(room, client)


async def test_a_model_that_returns_no_page_is_said_plainly(tmp_path):
    from heare.api.screens import ScreenMaker

    room, client, auth, settings = await _open(tmp_path)

    async def write(system: str, request: str) -> str:
        return "Вибач, не можу."

    room.core.add_executor(
        ScreenMaker(settings.screens_dir, write, room.core.state.emit)
    )
    task = room.core.delegate("зроби екран")
    await room.until(lambda: task.status == "done")
    assert task.result.startswith("Не вдалося")
    assert any(e.kind == "cannot" for e in room.core.state.journal)
    assert await (await client.get("/api/screens", headers=auth)).json() == []
    await _close(room, client)


class _Dials:
    """An output with controls, the way the voice has them."""

    name = "voice"
    abilities: frozenset[str] = frozenset()
    speaking = False

    def __init__(self):
        self.controls = {"mic": True, "speaker": True, "volume": 1.0}

    def set_control(self, name, value):
        if name == "volume":
            value = max(0.0, min(1.0, float(value)))
        self.controls[name] = value

    async def say(self, pieces):
        async for _ in pieces:
            pass
        return "done"

    def pause(self): ...
    def resume(self): ...
    def stop(self): ...


async def test_controls_are_listed_turned_and_announced(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    room.core.add_output(_Dials())
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["controls"] == {"voice": {"mic": True, "speaker": True, "volume": 1.0}}
    events = room.core.state.subscribe()
    turned = await client.post(
        "/api/controls", json={"part": "voice", "name": "mic", "value": False}, headers=auth
    )
    assert (await turned.json())["mic"] is False
    loud = await client.post(
        "/api/controls", json={"part": "voice", "name": "volume", "value": 7}, headers=auth
    )
    assert (await loud.json())["volume"] == 1.0
    assert [e.kind for e in (events.get_nowait(), events.get_nowait())] == ["control"] * 2
    missing = await client.post(
        "/api/controls", json={"part": "voice", "name": "warp", "value": 1}, headers=auth
    )
    assert missing.status == 404
    await _close(room, client)


async def test_the_stop_button_ends_the_reply_and_the_work_without_a_word(tmp_path):
    room, client, auth, _ = await _open(tmp_path, slow=0.05)
    worker = Worker()
    room.core.add_executor(worker)
    room.core.delegate("довга справа")
    room.model.will_say("Дуже довга відповідь, якої ніхто не дослухає до кінця.")
    room.says("Розкажи щось")
    await room.until(lambda: room.page.speaking and worker.tasks)
    stopped = await (await client.post("/api/stop", headers=auth)).json()
    assert stopped == {"tasks_stopped": 1}
    await room.settled()
    assert room.page.outcomes == ["stopped"] and worker.stopped == 1
    await _close(room, client)


async def test_the_state_carries_the_model_the_tally_and_recent_history(tmp_path):
    from heare.api.server import build as build_api

    room, client, auth, settings = await _open(tmp_path)
    await client.close()
    client = TestClient(TestServer(build_api(
        room.core, settings, lambda: {"provider": "deepseek", "wanted": "opencode",
                                      "fallback": True},
    )))
    await client.start_server()
    room.model.will_say("Чотири.")
    await client.post("/api/message", json={"text": "2+2?"}, headers=auth)
    await room.settled()
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["model"]["fallback"] is True
    assert state["session"]["turns"] == 1 and state["session"]["cost_usd"] == 0
    assert await (await client.get("/api/history", headers=auth)).json() == []
    await _close(room, client)


async def test_asking_to_change_the_drawing_hands_the_model_what_is_drawn(tmp_path):
    from heare.api.screens import ScreenMaker

    room, client, auth, settings = await _open(tmp_path)
    seen: list[str] = []

    async def write(system: str, request: str) -> str:
        seen.append(request)
        return "name: board\n<!doctype html><h1>Дошка</h1>" + ("<button>ок</button>" if len(seen) > 1 else "")

    maker = ScreenMaker(settings.screens_dir, write, room.core.state.emit)
    room.core.add_executor(maker)
    room.model.will_say("Є.", "Додав.")
    first = room.core.delegate("намалюй дошку")
    await room.until(lambda: first.status == "done")
    second = room.core.delegate("додай туди кнопку")
    await room.until(lambda: second.status == "done")
    assert "<h1>Дошка</h1>" in seen[1] and "board" in seen[1]
    assert await (await client.get("/api/screens", headers=auth)).json() == ["board"]
    assert "<button>" in await (await client.get("/screens/board", headers=auth)).text()
    await _close(room, client)


async def test_the_machine_can_be_read_and_a_widget_closed_by_hand(tmp_path):
    from heare.api.canvas import Canvas

    room, client, auth, _ = await _open(tmp_path)
    canvas = Canvas(tmp_path / "canvas.json", room.core.state.emit)
    room.core.add_output(canvas)
    canvas.put("cpu", "<h3>Процесор</h3>")
    machine = await (await client.get("/api/system", headers=auth)).json()
    assert machine["cpu"]["cores"] >= 1 and 0 <= machine["cpu"]["percent"] <= 100
    assert (await client.get("/api/system")).status == 401
    drawn = await (await client.get("/api/canvas", headers=auth)).json()
    assert drawn == {"blocks": [{"id": "cpu", "html": "<h3>Процесор</h3>", "size": "s"}], "css": ""}
    closed = await client.post("/api/canvas/remove", json={"id": "cpu"}, headers=auth)
    assert await closed.json() == {"removed": True}
    assert canvas.blocks == {}
    await _close(room, client)


async def test_the_person_can_redraw_a_widget_by_hand(tmp_path):
    from heare.api.canvas import Canvas

    room, client, auth, _ = await _open(tmp_path)
    canvas = Canvas(tmp_path / "canvas.json", room.core.state.emit)
    room.core.add_output(canvas)
    canvas.put("doodle", "<h3>Щось інше</h3><svg><circle cx='10' cy='10' r='4'/></svg>")
    redrawn = "<h3>Щось інше</h3><svg><circle cx='30' cy='20' r='8'/></svg>"
    drawn = await client.post("/api/canvas/put", json={"id": "doodle", "html": redrawn}, headers=auth)
    assert drawn.status == 200
    assert canvas.blocks["doodle"] == redrawn
    assert (await (await client.get("/api/canvas", headers=auth)).json())["blocks"][0]["html"] == redrawn
    bad = await client.post("/api/canvas/put", json={"id": "nope!", "html": "<p>x</p>"}, headers=auth)
    assert bad.status == 400
    await _close(room, client)


async def test_a_new_part_waits_and_is_approved(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    registration = asyncio.create_task(client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth))
    await asyncio.sleep(0.1)
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["bodies"]["voice@mac"]["state"] == "pending"

    approved = await client.post("/api/bodies/voice@mac/approve", headers=auth)
    assert approved.status == 200
    assert (await (await registration).json())["approved"] is True

    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["bodies"]["voice@mac"]["state"] == "approved"
    await _close(room, client)


async def test_a_rejected_part_is_not_used(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    registration = asyncio.create_task(client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth))
    await asyncio.sleep(0.1)
    await client.post("/api/bodies/voice@mac/reject", headers=auth)
    assert (await (await registration).json())["approved"] is False
    await _close(room, client)


async def test_a_remembered_part_does_not_ask_again(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    answer = await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth)
    assert (await answer.json())["approved"] is True
    await _close(room, client)


async def test_a_part_can_be_forgotten_and_must_ask_again(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth)

    forgotten = await client.post("/api/bodies/voice@mac/forget", headers=auth)
    assert forgotten.status == 200
    state = await (await client.get("/api/state", headers=auth)).json()
    assert "voice@mac" not in state["bodies"]
    assert json.loads((tmp_path / "parts.json").read_text()) == []

    again = asyncio.create_task(client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth))
    await asyncio.sleep(0.1)
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["bodies"]["voice@mac"]["state"] == "pending"
    await client.post("/api/bodies/voice@mac/reject", headers=auth)
    await again
    await _close(room, client)


async def test_a_part_gets_its_own_key_and_no_more(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)

    answer = await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input", "output"], "about": "",
        "key": "voice@mac", "host": "mac"}, headers=master)
    body = await answer.json()
    assert body["approved"] is True
    assert body["token"]
    own = {"X-Heare-Token": body["token"]}

    assert (await client.post("/api/input", json={
        "kind": "heard", "channel": "voice", "text": "привіт"},
        headers=own)).status == 200
    assert (await client.post("/api/input", json={
        "kind": "heard", "channel": "text", "text": "не моє"},
        headers=own)).status == 403

    for path in ("/api/keys", "/api/stop", "/api/settings", "/api/canvas"):
        assert (await client.post(path, json={}, headers=own)).status == 403, path

    other = await client.post("/api/bodies", json={
        "channel": "eyes", "key": "eyes@mac", "host": "mac"}, headers=own)
    assert other.status == 403

    await client.post("/api/bodies/voice@mac/forget", headers=master)
    assert (await client.post("/api/input", json={
        "kind": "heard", "channel": "voice", "text": "ще"},
        headers=own)).status == 401
    await _close(room, client)


async def test_a_folder_is_a_part_the_brain_already_knows(tmp_path):
    folder = tmp_path / "parts" / "demo"
    folder.mkdir(parents=True)
    (folder / "heare.json").write_text(json.dumps({
        "channel": "demo", "forms": ["input"], "about": "кіно",
        "ui": [{"name": "on", "kind": "toggle", "label": "Увімк", "value": False}],
    }), encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    pid = f"demo@{socket.gethostname()}"

    body = (await (await client.get("/api/state", headers=auth)).json()
            )["bodies"][pid]
    assert body["state"] == "away"
    assert body["ui"][0]["name"] == "on"

    knocked = await client.post("/api/bodies", json={
        "channel": "demo", "forms": ["input"], "about": "кіно"}, headers=auth)
    assert (await knocked.json())["approved"] is True
    body = (await (await client.get("/api/state", headers=auth)).json()
            )["bodies"][pid]
    assert body["state"] == "approved"
    assert body["ui"][0]["name"] == "on"
    await _close(room, client)


async def test_the_voice_body_says_when_the_sound_has_stopped(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    out = VoiceOut(room.core)
    room.core.add_output(out)
    await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input", "output"], "about": "",
        "key": "voice@mac", "host": "mac"}, headers=auth)

    await client.post("/api/input", json={
        "kind": "state", "channel": "voice",
        "values": {"speaking": True}}, headers=auth)
    assert out.speaking is True

    await client.post("/api/input", json={
        "kind": "state", "channel": "voice",
        "values": {"speaking": False}}, headers=auth)
    assert out.speaking is False
    await _close(room, client)


async def test_a_part_declares_the_controls_the_person_will_see(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    ui = [
        {"name": "mic", "kind": "toggle", "label": "Мікрофон", "value": True},
        {"name": "volume", "kind": "slider", "label": "Гучність",
         "value": 1.0, "min": 0, "max": 1, "step": 0.05},
    ]
    await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input", "output"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac", "ui": ui}, headers=auth)
    body = (await (await client.get("/api/state", headers=auth)).json()
            )["bodies"]["voice@mac"]
    assert body["ui"] == ui
    assert body["values"] == {}
    await _close(room, client)


async def test_turning_a_parts_control_is_a_wish_the_part_answers(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input", "output"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac",
        "ui": [{"name": "mic", "kind": "toggle", "label": "Мікрофон", "value": True}],
    }, headers=auth)
    events = room.core.state.subscribe()
    turned = await client.post("/api/controls", json={
        "part": "voice@mac", "name": "mic", "value": False}, headers=auth)
    assert turned.status == 200
    assert await turned.json() == {"ok": True}
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    wish = [e for e in seen if e.kind == "control"][-1]
    assert wish.data == {"channel": "voice", "name": "mic", "value": False,
                         "part": "voice@mac"}

    reported = await client.post("/api/input", json={
        "kind": "state", "channel": "voice", "values": {"mic": False}},
        headers=auth)
    assert reported.status == 200
    body = (await (await client.get("/api/state", headers=auth)).json()
            )["bodies"]["voice@mac"]
    assert body["values"] == {"mic": False}

    wrong = await client.post("/api/controls", json={
        "part": "voice@mac", "name": "warp", "value": 1}, headers=auth)
    assert wrong.status == 404
    await _close(room, client)


async def test_a_pairing_code_gives_a_machine_a_key_that_only_knocks(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    answer = await client.post(
        "/api/pair", json={"code": code, "kind": "machine", "name": "mac"})
    assert answer.status == 200
    given = await answer.json()
    assert given["pid"] == "machine@mac"
    machine = {"X-Heare-Token": given["token"]}
    assert (await client.get("/api/state", headers=machine)).status == 403
    assert (await client.get("/api/events", headers=machine)).status == 403
    await _close(room, client)


async def test_a_pairing_code_gives_a_window_the_person_s_powers(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    answer = await client.post(
        "/api/pair", json={"code": code, "kind": "window", "name": "phone"})
    assert answer.status == 200
    given = await answer.json()
    assert "token" not in given  # the browser never sees it: only the cookie
    assert (await client.get("/api/state")).status == 200
    await _close(room, client)


async def test_a_bad_or_used_code_is_refused(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    bad = await client.post("/api/pair", json={"code": "ZZZZZZZZ", "kind": "window"})
    assert bad.status == 401
    code = pair.make(Path(settings.pair_codes_file))
    assert (await client.post(
        "/api/pair", json={"code": code, "kind": "window"})).status == 200
    again = await client.post("/api/pair", json={"code": code, "kind": "window"})
    assert again.status == 401
    await _close(room, client)


async def test_too_many_tries_from_one_address_are_slow(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    for _ in range(10):
        tried = await client.post(
            "/api/pair", json={"code": "ZZZZZZZZ", "kind": "window"})
        assert tried.status == 401
    late = await client.post("/api/pair", json={"code": "ZZZZZZZZ", "kind": "window"})
    assert late.status == 429
    await _close(room, client)


async def test_the_page_mints_a_code_that_pairs_a_window_once(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    answer = await client.post("/api/pair-code", headers=auth)
    assert answer.status == 200
    given = await answer.json()
    assert len(given["code"]) == 8 and given["ttl"] == 300

    first = await client.post(
        "/api/pair", json={"code": given["code"], "kind": "window", "name": "phone"})
    assert first.status == 200
    again = await client.post(
        "/api/pair", json={"code": given["code"], "kind": "window", "name": "phone"})
    assert again.status == 401
    await _close(room, client)


async def test_a_pairing_code_is_not_minted_without_a_token(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    assert (await client.post("/api/pair-code")).status == 401
    await _close(room, client)


async def test_a_part_may_not_mint_a_pairing_code(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    part = {"X-Heare-Token": authlib.issue(
        Path(settings.part_tokens_file), "voice@mac")}
    assert (await client.post("/api/pair-code", headers=part)).status == 403
    await _close(room, client)


async def test_a_pairing_code_is_minted_once_every_two_seconds(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    assert (await client.post("/api/pair-code", headers=auth)).status == 200
    assert (await client.post("/api/pair-code", headers=auth)).status == 429
    await _close(room, client)


async def test_a_machine_key_knocks_for_its_part_and_the_part_gets_its_own(tmp_path):
    manifest = tmp_path / "parts" / "voice"
    manifest.mkdir(parents=True)
    (manifest / "heare.json").write_text(
        json.dumps({"channel": "voice", "host": "mac"}), encoding="utf-8")
    room = await open_room()
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts",
        part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json",
    )
    room.core.add_output(Screen(room.core))
    client = TestClient(TestServer(build(room.core, settings)))
    await client.start_server()
    code = pair.make(Path(settings.pair_codes_file))
    answer = await client.post(
        "/api/pair", json={"code": code, "kind": "machine", "name": "mac"})
    machine = {"X-Heare-Token": (await answer.json())["token"]}
    knock = await client.post("/api/bodies", headers=machine, json={
        "channel": "voice", "host": "mac", "forms": ["input"]})
    assert knock.status == 200
    granted = await knock.json()
    assert granted["approved"] is True
    assert granted["token"]
    await _close(room, client)


async def test_a_machine_key_knocks_without_a_second_card(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    given = await (await client.post(
        "/api/pair", json={"code": code, "kind": "machine", "name": "mac"})).json()
    knock = await client.post("/api/bodies", headers={
        "X-Heare-Token": given["token"]},
        json={"channel": "voice", "host": "mac", "forms": ["input"]})
    assert knock.status == 200
    answer = await knock.json()
    assert answer["approved"] is True and answer["token"]
    # Back with its own key, as after the brain restarted: still trusted,
    # no question waits on the page.
    again = await (await client.post("/api/bodies", headers={
        "X-Heare-Token": answer["token"]},
        json={"channel": "voice", "host": "mac", "forms": ["input"]})).json()
    assert again["approved"] is True
    await _close(room, client)


async def test_the_pairing_client_speaks_to_the_door(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    base = str(client.make_url("/")).rstrip("/")
    given = await asyncio.to_thread(pairing.pair, base, code, "machine", "mac")
    assert given["pid"] == "machine@mac" and given["token"]
    await _close(room, client)


async def test_two_windows_of_one_browser_do_not_revoke_each_other(tmp_path):
    room, client, _headers, settings = await _open(tmp_path)
    first = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "Chrome"})
    second = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "Chrome"})
    assert first.status == 200 and second.status == 200
    one, two = await first.json(), await second.json()
    assert one["pid"] != two["pid"]
    tokens = Path(settings.part_tokens_file)
    assert authlib.owner(tokens, first.cookies["heare_session"].value)
    assert authlib.owner(tokens, second.cookies["heare_session"].value)
    await _close(room, client)


async def test_two_open_pages_are_two_parts_named_after_themselves(tmp_path):
    room, client, headers, settings = await _open(tmp_path)
    knock = {"channel": "web", "forms": ["input", "output"],
             "about": "сторінка в браузері"}
    pids = []
    for name in ("laptop", "phone"):
        paired = await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "window", "name": name})
        window = {"X-Heare-Token": paired.cookies["heare_session"].value}
        answer = await (await client.post("/api/bodies", headers=window,
                                          json=knock)).json()
        # Approved without a question (the pairing was the question), and
        # without a new key: one would replace the window's cookie.
        assert answer["approved"] is True and "token" not in answer
        pids.append(answer["pid"])
        assert (await client.get("/api/state", headers=window)).status == 200

    bodies = (await (await client.get("/api/state", headers=headers)).json())["bodies"]
    assert pids[0] != pids[1] and set(pids) <= set(bodies)
    assert {bodies[pid]["host"] for pid in pids} == {
        pid.split("@", 1)[1] for pid in pids}
    await _close(room, client)


async def test_a_closed_page_is_away_and_comes_back_by_knocking(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, headers, settings = await _open(tmp_path)
    paired = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "laptop"})
    window = {"X-Heare-Token": paired.cookies["heare_session"].value}
    knock = {"channel": "web", "forms": ["input", "output"]}
    pid = (await (await client.post("/api/bodies", headers=window,
                                     json=knock)).json())["pid"]

    async def state() -> str:
        bodies = (await (await client.get("/api/state", headers=headers)).json())["bodies"]
        return bodies[pid]["state"]

    stream = await client.get("/api/events", headers=window)
    await stream.content.readany()
    assert await state() == "approved"
    stream.close()
    for _ in range(50):
        if await state() == "away":
            break
        await asyncio.sleep(0.02)
    assert await state() == "away"

    await client.post("/api/bodies", headers=window, json=knock)
    assert await state() == "approved"
    await _close(room, client)


async def test_one_page_s_switch_is_not_another_s(tmp_path):
    room, client, headers, settings = await _open(tmp_path)
    knock = {"channel": "web", "forms": ["input", "output"],
             "ui": [{"name": "aloud", "kind": "toggle", "value": False}]}
    windows, pids = [], []
    for name in ("laptop", "phone"):
        paired = await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "window", "name": name})
        window = {"X-Heare-Token": paired.cookies["heare_session"].value}
        answer = await (await client.post("/api/bodies", headers=window,
                                          json=knock)).json()
        windows.append(window)
        pids.append(answer["pid"])

    await client.post("/api/input", headers=windows[0], json={
        "kind": "state", "channel": "web", "values": {"aloud": True}})
    await client.post("/api/input", headers=windows[1], json={
        "kind": "state", "channel": "web", "values": {"aloud": False}})
    bodies = (await (await client.get("/api/state", headers=headers)).json())["bodies"]
    assert bodies[pids[0]]["values"]["aloud"] is True
    assert bodies[pids[1]]["values"]["aloud"] is False

    seen = room.core.state.subscribe()
    await client.post("/api/controls", headers=headers, json={
        "part": pids[1], "name": "aloud", "value": True})
    wish = await asyncio.wait_for(seen.get(), 1)
    assert wish.kind == "control" and wish.data["part"] == pids[1]
    await _close(room, client)


async def test_a_closed_page_leaves_the_list_after_a_while(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.02)
    monkeypatch.setattr(registry_mod, "WINDOW_GONE_S", 0.1)
    room, client, headers, settings = await _open(tmp_path)
    paired = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "laptop"})
    window = {"X-Heare-Token": paired.cookies["heare_session"].value}
    knock = {"channel": "web", "forms": ["input", "output"]}
    pid = (await (await client.post("/api/bodies", headers=window,
                                     json=knock)).json())["pid"]

    async def listed() -> bool:
        state = await (await client.get("/api/state", headers=headers)).json()
        return pid in state["bodies"]

    stream = await client.get("/api/events", headers=window)
    await stream.content.readany()
    stream.close()
    for _ in range(100):
        if not await listed():
            break
        await asyncio.sleep(0.02)
    assert not await listed()
    # The key was not taken: the page comes back by knocking.
    assert (await client.post("/api/bodies", headers=window, json=knock)).status == 200
    assert await listed()
    await _close(room, client)


async def test_a_page_that_listens_again_is_back_without_a_knock(tmp_path, monkeypatch):
    # After a brain restart the page reloads: its knock comes first, and the
    # end of its old stream is noticed after. The new stream must win.
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.02)
    room, client, headers, settings = await _open(tmp_path)
    paired = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "laptop"})
    window = {"X-Heare-Token": paired.cookies["heare_session"].value}
    pid = (await (await client.post("/api/bodies", headers=window, json={
        "channel": "web", "forms": ["input", "output"]})).json())["pid"]

    async def state() -> str:
        bodies = (await (await client.get("/api/state", headers=headers)).json())["bodies"]
        return bodies[pid]["state"]

    old = await client.get("/api/events", headers=window)
    await old.content.readany()
    old.close()
    for _ in range(50):
        if await state() == "away":
            break
        await asyncio.sleep(0.02)
    assert await state() == "away"

    new = await client.get("/api/events", headers=window)
    await new.content.readany()
    assert await state() == "approved"
    new.close()
    await _close(room, client)


async def _part_listening(client, headers, knock: dict) -> tuple[dict, object]:
    """Let a part in (trusted by its manifest-free approval) and open its stream."""
    asked = asyncio.create_task(client.post("/api/bodies", headers=headers, json=knock))
    await asyncio.sleep(0.05)
    await client.post(f"/api/bodies/{knock['key']}/approve", headers=headers)
    token = (await (await asked).json())["token"]
    stream = await client.get(f"/api/events?token={token}")
    await stream.content.readany()
    return token, stream


async def _heard(stream, room, *events) -> list[dict]:
    for kind, data in events:
        room.core.state.emit(kind, **data)
    got: list[dict] = []
    # A fixed window: the stream is never quiet, it says "still here".
    end = asyncio.get_running_loop().time() + 0.3
    while (left := end - asyncio.get_running_loop().time()) > 0:
        try:
            chunk = await asyncio.wait_for(stream.content.readany(), left)
        except TimeoutError:
            break
        for line in chunk.decode().splitlines():
            if line.startswith("data: "):
                got.append(json.loads(line[6:]))
    return [e for e in got if e["kind"] in {k for k, _ in events}]


async def test_a_part_hears_its_own_channel_not_the_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, headers, _ = await _open(tmp_path)
    _, stream = await _part_listening(client, headers, {
        "channel": "eyes", "forms": ["input"], "key": "eyes@mac"})
    got = await _heard(stream, room,
                       ("heard", {"text": "мій пароль", "channel": "voice"}),
                       ("control", {"channel": "eyes", "name": "looking", "value": False}))
    assert [e["kind"] for e in got] == ["control"]
    stream.close()
    await _close(room, client)


async def test_a_chat_that_shows_the_conversation_hears_it(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, headers, _ = await _open(tmp_path)
    _, stream = await _part_listening(client, headers, {
        "channel": "text", "forms": ["input", "output"], "key": "text@mac",
        "hears": "all"})
    got = await _heard(stream, room, ("said", {"text": "Готово.", "channel": "voice"}))
    assert [e["text"] for e in got] == ["Готово."]
    bodies = (await (await client.get("/api/state", headers=headers)).json())["bodies"]
    assert bodies["text@mac"]["hears"] == "all"
    stream.close()
    await _close(room, client)


async def test_a_part_of_another_wire_is_told_why(tmp_path):
    room, client, headers, _ = await _open(tmp_path)
    answer = await (await client.post("/api/bodies", headers=headers, json={
        "v": 99, "channel": "eyes", "key": "eyes@mac"})).json()
    assert answer["approved"] is False and "v99" in answer["why"]
    await _close(room, client)


async def test_the_brain_says_its_version_and_a_part_s_is_kept(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac", "version": "0.0.9"}, headers=auth)

    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["running"]["version"] == metadata.version("heare-brain")
    assert "commit" in state["running"]
    assert state["bodies"]["voice@mac"]["version"] == "0.0.9"
    await _close(room, client)


def test_an_installed_brain_does_not_invent_a_commit(monkeypatch):
    monkeypatch.setattr(api_server, "__file__",
                        "/opt/venv/lib/python3.12/site-packages/heare/api/server.py")
    assert api_server.running()["commit"] is None


async def test_the_mind_snapshot_reads_facts_reminders_and_the_profile(tmp_path):
    import os
    import time
    from datetime import datetime

    from heare.engine import Intents
    from heare.memory.contracts import CONFIRMED, Fact
    from heare.memory.facts import SQLiteFacts
    from heare.memory.profile import Profile

    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    memory_dir = tmp_path / "memory"
    facts.add(Fact(
        subject="owner", predicate="name", value="Олена",
        text="Тебе звати Олена.", source="legacy", trust="legacy",
        confidence=0.9, valid_from=1690000000.0, created_at=1690000000.0,
    ))
    confirmed = facts.add(Fact(
        subject="owner", predicate="city", value="Київ",
        text="Живеш у Києві.", source="user_said", trust="user_confirmed",
        status=CONFIRMED, confidence=1.0, valid_from=1680000000.0,
        created_at=1680000000.0,
    ))
    newest = 0
    for step in range(54):
        newest = facts.add(Fact(
            subject="owner", predicate="note", value=str(step),
            text=f"Нотатка {step}.", source="user_said", trust="user_said",
            confidence=0.5, valid_from=1690000001.0 + step,
            created_at=1690000001.0 + step,
        ))
    soon = time.time() + 100.0
    reminder = intents.hold(
        "подзвонити мамі", "reminder", 3, about="reminder",
        not_before=soon, until=soon + 86400.0)
    Profile(memory_dir).write("user", "Олена.")
    (memory_dir / "review.json").write_text(
        '{"day": "2026-10-22"}', encoding="utf-8")
    stamped = memory_dir / "consolidate.json"
    stamped.write_text("{}", encoding="utf-8")
    os.utime(stamped, (1690000000.0, 1690000000.0))

    room, client, auth, _ = await _open(
        tmp_path, facts=facts, intents=intents, memory_dir=memory_dir)
    seen = await (await client.get("/api/mind", headers=auth)).json()

    assert seen["counts"] == {"pending": 55, "confirmed": 1, "reminders": 1}
    assert len(seen["facts"]["pending"]) == 50
    assert seen["facts"]["pending"][0]["id"] == newest
    assert seen["facts"]["pending"][0]["status"] == "pending"
    assert [f["id"] for f in seen["facts"]["confirmed"]] == [confirmed]
    assert seen["facts"]["confirmed"][0]["status"] == "confirmed"
    assert seen["reminders"] == [{
        "id": reminder.id, "what": "подзвонити мамі", "about": "reminder",
        "not_before": soon, "until": soon + 86400.0,
        "made": reminder.made, "weight": 3,
    }]
    assert seen["review"] == {"last": "2026-10-22", "hour": 4}
    assert seen["consolidation"]["last"] == datetime.fromtimestamp(
        1690000000.0).replace(microsecond=0).isoformat()
    assert seen["profile"] == {
        "blocks": {"user": "Олена.", "threads": "", "goals": ""},
        "limits": {"user": 1200, "threads": 1000, "goals": 600},
    }
    await _close(room, client)


async def test_a_fact_is_confirmed_or_rejected_through_the_mind(tmp_path):
    from heare.engine import Intents
    from heare.memory.contracts import CONFIRMED, REJECTED, Fact
    from heare.memory.facts import SQLiteFacts

    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    one = facts.add(Fact(
        subject="owner", predicate="name", value="Олена",
        text="Тебе звати Олена.", source="legacy", trust="legacy"))
    two = facts.add(Fact(
        subject="owner", predicate="city", value="Київ",
        text="Живеш у Києві.", source="legacy", trust="legacy"))
    room, client, auth, _ = await _open(
        tmp_path, facts=facts, intents=intents)
    events = room.core.state.subscribe()

    answer = await client.post(
        f"/api/mind/facts/{one}/confirm", headers=auth)
    assert answer.status == 200 and await answer.json() == {"ok": True}
    assert facts.get(one).status == CONFIRMED

    answer = await client.post(
        f"/api/mind/facts/{two}/reject", headers=auth)
    assert answer.status == 200 and await answer.json() == {"ok": True}
    assert facts.get(two).status == REJECTED
    again = await client.post(
        f"/api/mind/facts/{two}/confirm", headers=auth)
    assert again.status == 404

    assert (await client.post(
        "/api/mind/facts/9999/confirm", headers=auth)).status == 404
    assert (await client.post(
        "/api/mind/facts/9999/reject", headers=auth)).status == 404

    seen = []
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "mind":
            seen.append(event.data)
    assert seen == [
        {"what": "fact", "action": "confirmed", "id": one},
        {"what": "fact", "action": "rejected", "id": two},
    ]
    await _close(room, client)


async def test_a_reminder_is_cancelled_through_the_mind(tmp_path):
    from heare.engine import Intents
    from heare.memory.facts import SQLiteFacts

    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    reminder = intents.hold("подзвонити мамі", "reminder", 3,
                            about="reminder", not_before=1690000000.0)
    room, client, auth, _ = await _open(
        tmp_path, facts=facts, intents=intents)
    events = room.core.state.subscribe()

    answer = await client.post(
        f"/api/mind/reminders/{reminder.id}/cancel", headers=auth)
    assert answer.status == 200 and await answer.json() == {"ok": True}
    assert intents.reminders(1690000000.0) == []
    assert (await client.post(
        f"/api/mind/reminders/{reminder.id}/cancel", headers=auth)).status == 404
    assert (await client.post(
        "/api/mind/reminders/9999/cancel", headers=auth)).status == 404

    seen = []
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "mind":
            seen.append(event.data)
    assert seen == [{"what": "reminder", "action": "cancelled",
                     "id": reminder.id}]
    await _close(room, client)


async def test_the_profile_is_saved_through_the_mind(tmp_path):
    from heare.engine import Intents
    from heare.memory.facts import SQLiteFacts

    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    room, client, auth, settings = await _open(
        tmp_path, facts=facts, intents=intents)
    memory_dir = Path(settings.memory_dir)

    answer = await client.put("/api/mind/profile", headers=auth,
                              json={"key": "goals", "text": "  Вивчити SQL.  "})
    assert answer.status == 200 and await answer.json() == {"ok": True}
    assert (memory_dir / "goals.md").read_text(encoding="utf-8") == "Вивчити SQL."
    seen = await (await client.get("/api/mind", headers=auth)).json()
    assert seen["profile"]["blocks"]["goals"] == "Вивчити SQL."

    assert (await client.put("/api/mind/profile", headers=auth,
                             json={"key": "secrets", "text": "x"})).status == 400
    assert (await client.put("/api/mind/profile", headers=auth,
                             json={"key": "user", "text": 5})).status == 400
    assert (await client.put("/api/mind/profile", headers=auth,
                             json={"key": "user", "text": "x" * 1201})).status == 400
    await _close(room, client)


async def test_the_mind_says_the_store_is_missing_instead_of_failing(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    answer = await client.get("/api/mind", headers=auth)
    assert answer.status == 503 and "memory" in await answer.text()
    for path in ("/api/mind/journal", "/api/mind/search?q=щось"):
        refused = await client.get(path, headers=auth)
        assert refused.status == 503 and "memory" in await refused.text()
    for path in ("/api/mind/facts/1/confirm", "/api/mind/facts/1/reject",
                 "/api/mind/reminders/1/cancel"):
        refused = await client.post(path, headers=auth)
        assert refused.status == 503 and "memory" in await refused.text()
    await _close(room, client)


async def test_the_journal_lists_events_newest_first_with_ukrainian_titles(
        tmp_path):
    from datetime import datetime

    from heare.engine import Intents
    from heare.memory.facts import SQLiteFacts
    from heare.memory.journal import Journal

    journal = Journal(tmp_path / "events")
    base = 1_690_000_000.0
    heard = journal.append(
        "heard", {"text": "привіт", "channel": "voice"},
        source="voice", trust="user_said", at=base)
    task = journal.append(
        "task", {"id": 1, "request": "зроби", "status": "running"},
        source="hands", trust="tool_output", at=base + 1)
    permission = journal.append(
        "permission", {"id": 2, "what": "записати"},
        source="brain", trust="system", at=base + 2)
    unknown = journal.append(
        "mystery", {"name": "таємниця"},
        source="core", trust="system", at=base + 3)
    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    room, client, auth, _ = await _open(
        tmp_path, facts=facts, intents=intents, journal=journal)

    items = (await (await client.get(
        "/api/mind/journal", headers=auth)).json())["items"]

    assert [item["id"] for item in items] == [
        unknown["id"], permission["id"], task["id"], heard["id"],
    ]
    assert [item["title"] for item in items] == [
        "mystery", "дозвіл", "справа", "почуто",
    ]
    assert items[0]["kind"] == "mystery"
    assert items[0]["text"] == "таємниця"
    assert items[1]["text"] == "записати"
    assert items[2]["text"] == "running"
    assert items[3]["text"] == "привіт"
    assert "voice" in items[3]["meta"] and "user_said" in items[3]["meta"]
    assert "hands" in items[2]["meta"] and "tool_output" in items[2]["meta"]
    assert "running" in items[2]["meta"]
    assert datetime.fromisoformat(items[0]["at"]).tzinfo is not None
    await _close(room, client)


async def test_the_journal_limit_is_clamped_and_must_be_an_integer(tmp_path):
    from heare.engine import Intents
    from heare.memory.facts import SQLiteFacts
    from heare.memory.journal import Journal

    journal = Journal(tmp_path / "events")
    for step in range(3):
        journal.append(
            "heard", {"text": f"рядок {step}"},
            source="voice", trust="user_said", at=1_690_000_000.0 + step)
    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    room, client, auth, _ = await _open(
        tmp_path, facts=facts, intents=intents, journal=journal)

    async def ids(query: str) -> list[str]:
        found = await (await client.get(
            f"/api/mind/journal?{query}", headers=auth)).json()
        return [item["text"] for item in found["items"]]

    assert await ids("limit=2") == ["рядок 2", "рядок 1"]
    assert await ids("limit=0") == ["рядок 2"]
    assert await ids("limit=999") == ["рядок 2", "рядок 1", "рядок 0"]
    assert (await client.get(
        "/api/mind/journal?limit=two", headers=auth)).status == 400
    await _close(room, client)


async def test_the_journal_and_search_need_their_stores(tmp_path):
    from heare.engine import Intents
    from heare.memory.facts import SQLiteFacts

    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    room, client, auth, _ = await _open(tmp_path, facts=facts, intents=intents)

    journal = await client.get("/api/mind/journal", headers=auth)
    assert journal.status == 503 and "memory" in await journal.text()
    found = await client.get(
        "/api/mind/search", params={"q": "щось"}, headers=auth)
    assert found.status == 503 and "memory" in await found.text()
    await _close(room, client)


async def test_search_returns_the_spoken_answer_and_the_same_fragments(
        tmp_path, monkeypatch):
    from datetime import datetime as real_datetime

    from heare.engine import Intents
    from heare.memory import persist
    from heare.memory import book as book_module
    from heare.memory.book import Book
    from heare.memory.facts import SQLiteFacts

    old = real_datetime(2026, 10, 20, 9, 0, 0).timestamp()

    class Clock:
        @staticmethod
        def time() -> float:
            return old

    monkeypatch.setattr(persist, "time", Clock)

    class Fixed(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 20, 12, 0, 0)

    monkeypatch.setattr(book_module, "datetime", Fixed)

    book = Book(
        tmp_path / "heare.db", names=("дока",),
        profile_dir=tmp_path / "memory")
    try:
        await book.keep("user", "Мій план на відпустку — Карпати.", "text")
        await book.keep("assistant", "Відпустка в Карпатах — це добре.", "text")
        facts = SQLiteFacts(tmp_path / "facts.db")
        intents = Intents(tmp_path / "intents.json")
        room, client, auth, _ = await _open(
            tmp_path, facts=facts, intents=intents, memory=book)

        found = await (await client.get(
            "/api/mind/search", params={"q": "відпустка"},
            headers=auth)).json()
        said, expected = await book.look_up("відпустка")

        assert found == {"said": said, "items": expected}
        assert found["said"] == (
            "Сьогодні прозвучало: Відпустка в Карпатах — це добре.")
        assert [item["id"] for item in found["items"]] == [
            f"search-{index}" for index in range(len(found["items"]))
        ]
        assert len(found["items"]) == 2
        assert {item["title"] for item in found["items"]} == {
            "ти казав", "heare",
        }
        assert {item["text"] for item in found["items"]} == {
            "Мій план на відпустку — Карпати.",
            "Відпустка в Карпатах — це добре.",
        }
        assert all(item["meta"] == [] for item in found["items"])
        assert all(
            Fixed.fromisoformat(item["at"]).tzinfo is not None
            for item in found["items"])

        assert (await client.get(
            "/api/mind/search", headers=auth)).status == 400
        assert (await client.get(
            "/api/mind/search", params={"q": "   "},
            headers=auth)).status == 400
        assert (await client.get(
            "/api/mind/search", params={"q": "x" * 501},
            headers=auth)).status == 400
    finally:
        book.close()
    await _close(room, client)


async def test_a_forgotten_event_leaves_the_journal_and_the_search(
        tmp_path, monkeypatch):
    from datetime import datetime as real_datetime

    from heare.engine import Intents
    from heare.memory import persist
    from heare.memory import book as book_module
    from heare.memory.book import Book
    from heare.memory.facts import SQLiteFacts
    from heare.memory.journal import Journal

    old = real_datetime(2026, 10, 20, 9, 0, 0).timestamp()

    class Clock:
        @staticmethod
        def time() -> float:
            return old

    monkeypatch.setattr(persist, "time", Clock)

    class Fixed(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 20, 12, 0, 0)

    monkeypatch.setattr(book_module, "datetime", Fixed)

    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", names=("дока",),
        profile_dir=tmp_path / "memory", journal=journal)
    try:
        await book.keep("user", "Мій план на відпустку — Карпати.", "text")
        heard = next(e for e in journal.events() if e["kind"] == "heard")
        facts = SQLiteFacts(tmp_path / "facts.db")
        intents = Intents(tmp_path / "intents.json")
        room, client, auth, _ = await _open(
            tmp_path, facts=facts, intents=intents, journal=journal, memory=book)

        found = await (await client.get(
            "/api/mind/search", params={"q": "відпустка"}, headers=auth)).json()
        assert found["items"]

        answer = await client.post(
            "/api/mind/journal/forget", json={"ids": [heard["id"]]},
            headers=auth)
        assert answer.status == 200
        body = await answer.json()
        assert body["ok"] is True and body["forgotten"] >= 1

        items = (await (await client.get(
            "/api/mind/journal", headers=auth)).json())["items"]
        assert heard["id"] not in [item["id"] for item in items]
        assert "forget" in [item["kind"] for item in items]

        found = await (await client.get(
            "/api/mind/search", params={"q": "відпустка"}, headers=auth)).json()
        assert all("відпустка" not in item["text"] for item in found["items"])
        await _close(room, client)
    finally:
        book.close()


async def test_forget_refuses_bad_bodies(tmp_path):
    from heare.engine import Intents
    from heare.memory.book import Book
    from heare.memory.facts import SQLiteFacts
    from heare.memory.journal import Journal

    journal = Journal(tmp_path / "events")
    book = Book(
        tmp_path / "heare.db", names=("дока",),
        profile_dir=tmp_path / "memory", journal=journal)
    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    room, client, auth, _ = await _open(
        tmp_path, facts=facts, intents=intents, journal=journal, memory=book)
    valid = "ev_" + "0" * 32

    async def status(payload: object) -> int:
        response = await client.post(
            "/api/mind/journal/forget", json=payload, headers=auth)
        return response.status

    try:
        assert await status({}) == 400
        assert await status({"ids": []}) == 400
        assert await status({"ids": valid}) == 400
        assert await status({"ids": ["x"]}) == 400
        assert await status({"ids": [valid] * 51}) == 400
        assert await status({"ids": [valid]}) == 200
    finally:
        await _close(room, client)
        book.close()


async def test_forget_needs_the_memory_store(tmp_path):
    from heare.engine import Intents
    from heare.memory.facts import SQLiteFacts

    facts = SQLiteFacts(tmp_path / "facts.db")
    intents = Intents(tmp_path / "intents.json")
    room, client, auth, _ = await _open(tmp_path, facts=facts, intents=intents)

    answer = await client.post(
        "/api/mind/journal/forget", json={"ids": ["ev_" + "0" * 32]},
        headers=auth)
    assert answer.status == 503 and "memory" in await answer.text()
    await _close(room, client)


async def test_features_are_listed_and_acted_on_through_the_door(tmp_path):
    from heare.features import Feature, Features

    events: list[dict] = []

    def emit(kind: str, **data: object) -> None:
        events.append({"kind": kind, **data})

    registry = Features(emit)
    number = {"value": 1}

    def read() -> dict[str, object]:
        return {"state": "ready", "note": str(number["value"]),
                "controls": [{"kind": "button", "name": "run", "label": "Бігти"}]}

    async def write(name: str, value: object) -> None:
        if name != "bump":
            raise ValueError(f"unknown action: {name}")
        number["value"] += int(value)

    registry.add(Feature(
        id="demo", title="Демо", about="Тестова фіча.", group="memory",
        order=10, read=read, write=write))

    room, client, auth, _ = await _open(tmp_path, features=registry)

    listed = await (await client.get("/api/features", headers=auth)).json()
    assert listed["groups"] == [{"id": "memory", "title": "Памʼять",
                                 "order": 10}]
    assert listed["features"][0]["id"] == "demo"
    assert listed["features"][0]["controls"][0]["name"] == "run"

    answer = await client.post(
        "/api/features/demo/act", headers=auth,
        json={"name": "bump", "value": 1})
    assert answer.status == 200
    called = await answer.json()
    assert called["ok"] is True
    assert called["feature"]["note"] == "2"
    assert {"kind": "feature", "id": "demo", "state": "changed"} in events

    assert (await client.post(
        "/api/features/nope/act", headers=auth,
        json={"name": "bump", "value": 1})).status == 404
    bad = await client.post(
        "/api/features/demo/act", headers=auth,
        json={"name": "nope", "value": 1})
    assert bad.status == 400 and "unknown" in await bad.text()
    assert (await client.post(
        "/api/features/demo/act", headers=auth,
        json={"name": "", "value": 1})).status == 400
    await _close(room, client)


async def test_features_are_empty_without_a_registry(tmp_path):
    room, client, auth, _ = await _open(tmp_path)

    assert await (await client.get(
        "/api/features", headers=auth)).json() == {
            "groups": [], "features": []}
    assert (await client.post(
        "/api/features/demo/act", headers=auth,
        json={"name": "run", "value": True})).status == 404
    await _close(room, client)


async def test_what_the_ear_heard_is_listed_heard_and_marked(tmp_path):
    from heare.channels.voice.heard import Heard

    room, client, headers, settings = await _open(tmp_path)
    ear = Heard(Path(settings.heard_dir))
    ear.add(b"\x00\x10" * 800, {"text": "Та", "turn": 7, "dropped": None})
    ear.add(b"\x00\x10" * 800, {"text": "можню.", "turn": 7, "dropped": None})
    ear.add(b"\x00\x10" * 800, {"text": "Дякую за перегляд!", "turn": 8, "dropped": "invented"})

    listed = await (await client.get("/api/heard", headers=headers)).json()
    assert listed["total"] == 2 and listed["marked"] == 0
    newest, older = listed["replies"]
    assert newest["dropped"] and newest["text"] == ""
    assert older["text"] == "Та можню." and len(older["pieces"]) == 2

    sound = await client.get(f"/api/heard/{older['pieces'][0]['name']}.wav", headers=headers)
    assert sound.status == 200 and (await sound.read())[:4] == b"RIFF"
    assert (await client.get("/api/heard/..%2Fconfig.wav", headers=headers)).status == 404

    fixed = await client.post("/api/heard/7", headers=headers,
                              json={"label": "me", "truth": "Та можна."})
    assert (await fixed.json())["marked"] == 2
    noise = await client.post("/api/heard/8", headers=headers, json={"label": "background"})
    assert noise.status == 200
    assert (await client.post("/api/heard/8", headers=headers,
                              json={"label": "maybe"})).status == 400
    assert (await client.post("/api/heard/99", headers=headers,
                              json={"label": "me"})).status == 404

    listed = await (await client.get("/api/heard", headers=headers)).json()
    assert listed["marked"] == 2
    assert [r["label"] for r in listed["replies"]] == ["background", "me"]
    assert listed["replies"][1]["truth"] == "Та можна."
    assert (await client.get("/api/heard")).status == 401
    await _close(room, client)
