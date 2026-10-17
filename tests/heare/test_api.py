"""The API, driven the way a screen drives it."""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from heare.api.server import Screen, VoiceOut, build
from heare.config import Settings
from tests.heare.fakes import Says, Worker, open_room


async def _open(tmp_path, **room_kw):
    room = await open_room(**room_kw)
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens",
    )
    room.core.add_output(Screen(room.core))
    client = TestClient(TestServer(build(room.core, settings)))
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
    assert before["DEEPSEEK_API_KEY"] is False
    saved = await client.post(
        "/api/keys", json={"name": "DEEPSEEK_API_KEY", "value": "sk-secret-123"}, headers=auth
    )
    assert saved.status == 200
    after = await client.get("/api/keys", headers=auth)
    text = await after.text()
    assert json.loads(text)["DEEPSEEK_API_KEY"] is True
    assert "sk-secret-123" not in text
    for path in ("/api/settings", "/api/state"):
        assert "sk-secret-123" not in await (await client.get(path, headers=auth)).text()
    refused = await client.post("/api/keys", json={"name": "PATH", "value": "x"}, headers=auth)
    assert refused.status == 400
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
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
    shown = await (await client.get("/screens/costs")).text()
    assert "<h1>Витрати</h1>" in shown and "HEARE_TOKEN" in shown
    bad = await client.put("/api/screens/..%2Fevil", json={"html": "x"}, headers=auth)
    assert bad.status in (400, 404)
    assert not (tmp_path / "evil.html").exists()
    await _close(room, client)


async def test_the_built_in_page_is_served(tmp_path):
    room, client, _, _ = await _open(tmp_path)
    page = await (await client.get("/")).text()
    assert "HEARE_TOKEN" in page and "Керування" in page
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
    assert "<h1>Справи</h1>" in await (await client.get("/screens/my-tasks")).text()
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
    assert "<button>" in await (await client.get("/screens/board")).text()
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


async def test_a_body_registers_and_the_brain_knows_it(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    response = await client.post("/api/bodies", json={
        "channel": "voice", "forms": ["input", "output"],
        "about": "мікрофон і динамік",
    }, headers=auth)
    assert response.status == 200
    state = await (await client.get("/api/state", headers=auth)).json()
    assert state["bodies"]["voice"]["forms"] == ["input", "output"]
    await _close(room, client)
