"""The API, driven the way a screen drives it."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import time
from importlib import metadata
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from heare import pairing
from heare.api import auth as authlib, pair
from heare.api import registry as registry_mod
from heare.api import server as api_server
from heare.api.server import Screen, VoiceOut, build
from heare.config import Settings
from heare.core import PROTOCOL
from heare.model.providers import PROVIDERS
from tests.heare.fakes import Says, Worker, open_room


async def _open(tmp_path, *, facts=None, intents=None, memory_dir=None,
                features=None, journal=None, memory=None, make_executor=None,
                **room_kw):
    room = await open_room(**room_kw)
    settings = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts",
        part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json",
        memory_dir=memory_dir if memory_dir is not None else tmp_path / "memory",
        heard_dir=tmp_path / "heard",
        # Never the developer's own ~/.heare: the backup door works on it.
        backups_dir=tmp_path / "home" / "backups",
    )
    room.core.add_output(Screen(room.core))
    mind = None
    if facts is not None and intents is not None:
        from heare.mind import Mind
        mind = Mind(facts, intents, settings, journal=journal, memory=memory)
    client = TestClient(TestServer(
        build(room.core, settings, mind=mind, features=features,
              engine=room.extra.get("engine"),
              make_executor=make_executor)))
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


async def test_a_heard_line_carries_the_machine_that_heard_it(tmp_path, monkeypatch):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, settings = await _open(tmp_path)
    voice = await _part(client, auth, "voice@mac", approves=False)
    heard = []
    monkeypatch.setattr(room.core.port, "heard", heard.append)
    assert (await client.post("/api/input", json={
        "kind": "heard", "channel": "voice", "text": "привіт"},
        headers=voice)).status == 200
    assert [m.origin for m in heard] == ["mac"]

    paired = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "laptop"})
    window = {"X-Heare-Token": paired.cookies["heare_session"].value}
    await client.post("/api/bodies", headers=window, json={
        "v": PROTOCOL,
        "channel": "web", "forms": ["input", "output"]})
    assert (await client.post("/api/input", json={
        "kind": "heard", "channel": "web", "text": "привіт"},
        headers=window)).status == 200
    assert [m.origin for m in heard] == ["mac", ""]
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
    assert around[-1].data == {
        "key": "playing", "value": ["Music"], "channel": "eyes",
    }
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


async def _part(client, master, pid, *, approves):
    channel, host = pid.split("@")
    answer = await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": channel, "forms": ["output"], "about": "", "key": pid,
        "host": host, "approves": approves}, headers=master)
    return {"X-Heare-Token": (await answer.json())["token"]}


async def _asking(room):
    worker = Worker(asks=(("записати файл", "change"),))
    room.core.add_executor(worker)
    room.core.delegate("запиши")
    await room.until(lambda: room.core.state.permission is not None)
    return worker, room.core.state.permission.id


async def test_a_trusted_panel_with_a_button_says_yes(tmp_path):
    (tmp_path / "parts.json").write_text('["oled@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    panel = await _part(client, master, "oled@desk", approves=True)
    worker, asked = await _asking(room)

    answer = await client.post(f"/api/permission/{asked}", json={"allow": True}, headers=panel)

    assert (await answer.json()) == {"answered": True}
    await room.until(lambda: worker.allowed)
    assert worker.allowed == [True]
    await _close(room, client)


async def test_a_button_never_says_no_and_never_answers_another_question(tmp_path):
    (tmp_path / "parts.json").write_text('["oled@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    panel = await _part(client, master, "oled@desk", approves=True)
    worker, asked = await _asking(room)

    no = await client.post(f"/api/permission/{asked}", json={"allow": False}, headers=panel)
    stale = await client.post(f"/api/permission/{asked + 1}", json={"allow": True}, headers=panel)

    assert no.status == 403
    assert (await stale.json()) == {"answered": False}
    assert worker.allowed == []
    await _close(room, client)


async def test_a_part_without_a_yes_of_its_own_may_not_answer(tmp_path):
    (tmp_path / "parts.json").write_text('["eyes@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    eyes = await _part(client, master, "eyes@desk", approves=False)
    _worker, asked = await _asking(room)

    answer = await client.post(f"/api/permission/{asked}", json={"allow": True}, headers=eyes)

    assert answer.status == 403
    await _close(room, client)


async def test_a_key_can_be_saved_and_never_read(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    async def alive(name, value, timeout=10.0):
        return True, "alive"

    monkeypatch.setattr(api_server, "check_key", alive)
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


async def test_a_bad_key_from_the_pult_is_not_stored(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    async def refused(name, value, timeout=10.0):
        return False, "the key does not fit"

    monkeypatch.setattr(api_server, "check_key", refused)
    room, client, auth, _ = await _open(tmp_path)
    bad = await client.post(
        "/api/keys", json={"name": "DEEPSEEK_API_KEY", "value": "sk-bad"},
        headers=auth,
    )

    assert bad.status == 400
    assert "the key does not fit" in await bad.text()
    report = await (await client.get("/api/keys", headers=auth)).json()
    assert report["DEEPSEEK_API_KEY"] == {
        "source": None, "env": False, "stored": False,
    }
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


async def test_a_screen_is_served_sandboxed_and_cannot_reach_the_api(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    await client.put("/api/screens/costs", json={
        "html": "<script>fetch('/api/state')</script>"}, headers=auth)
    shown = await client.get("/screens/costs", headers=auth)
    policy = shown.headers.get("Content-Security-Policy", "")
    assert "sandbox" in policy and "allow-scripts" in policy
    assert "allow-same-origin" not in policy and "default-src 'none'" in policy
    await _close(room, client)


async def test_the_built_in_page_is_served(tmp_path):
    room, client, _, _ = await _open(tmp_path)
    page = await (await client.get("/")).text()
    assert "HEARE_TOKEN" not in page and "Controls" in page
    assert 'id="pairing"' in page  # a browser without a key asks for a code
    assert "Devices" in page and "Connect a device" in page
    await _close(room, client)


async def test_the_page_brings_its_own_fonts(tmp_path):
    room, client, _, _ = await _open(tmp_path)
    page = await (await client.get("/page.css")).text()
    named = set(re.findall(r"url\(/fonts/([a-z-]+)\.woff2\)", page))
    assert named == {"onest-cyrillic", "onest-latin", "unbounded-cyrillic", "unbounded-latin"}
    for name in named:  # no key: a font is no secret, and the page loads it before pairing
        got = await client.get(f"/fonts/{name}.woff2")
        assert got.status == 200 and got.content_type == "font/woff2"
        assert (await got.read())[:4] == b"wOF2"
    assert (await client.get("/fonts/missing.woff2")).status == 404
    assert (await client.get("/fonts/..%2Fpage.html")).status == 404
    await _close(room, client)


async def test_the_page_brings_its_own_script_and_style(tmp_path):
    room, client, _, _ = await _open(tmp_path)
    css = await client.get("/page.css")
    assert css.status == 200 and css.content_type == "text/css"
    js = await client.get("/page.js")
    assert js.status == 200 and js.content_type in ("text/javascript", "application/javascript")
    assert "const LOCALE" in await js.text()
    assert "@font-face" in await css.text()
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
    from heare.api.images import ImageMaker

    room, client, auth, settings = await _open(tmp_path)

    async def write(system: str, request: str) -> str:
        assert "sandbox" in system  # the model is told the screen cannot call the API
        return "name: my-tasks\n<!doctype html><h1>Справи</h1>"

    room.core.add_executor(
        ImageMaker(settings.screens_dir, write, room.core.state.emit)
    )
    room.model.will_say("Екран готовий.")
    task = room.core.delegate("зроби екран зі справами")
    await room.until(lambda: task.status == "done")
    assert "my-tasks" in task.result
    assert await (await client.get("/api/screens", headers=auth)).json() == ["my-tasks"]
    assert "<h1>Справи</h1>" in await (await client.get("/screens/my-tasks", headers=auth)).text()
    await _close(room, client)


async def test_a_model_that_returns_no_page_is_said_plainly(tmp_path):
    from heare.api.images import ImageMaker

    room, client, auth, settings = await _open(tmp_path)

    async def write(system: str, request: str) -> str:
        return "Вибач, не можу."

    room.core.add_executor(
        ImageMaker(settings.screens_dir, write, room.core.state.emit)
    )
    task = room.core.delegate("зроби екран")
    await room.until(lambda: task.status == "done")
    assert task.result.startswith("Failed")
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
    from heare.api.images import ImageMaker

    room, client, auth, settings = await _open(tmp_path)
    seen: list[str] = []

    async def write(system: str, request: str) -> str:
        seen.append(request)
        return "name: board\n<!doctype html><h1>Дошка</h1>" + ("<button>ок</button>" if len(seen) > 1 else "")

    maker = ImageMaker(settings.screens_dir, write, room.core.state.emit)
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
        "v": PROTOCOL,
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
        "v": PROTOCOL,
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
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth)
    assert (await answer.json())["approved"] is True
    await _close(room, client)


async def test_a_part_can_be_forgotten_and_must_ask_again(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac"}, headers=auth)

    forgotten = await client.post("/api/bodies/voice@mac/forget", headers=auth)
    assert forgotten.status == 200
    state = await (await client.get("/api/state", headers=auth)).json()
    assert "voice@mac" not in state["bodies"]
    assert json.loads((tmp_path / "parts.json").read_text()) == []

    again = asyncio.create_task(client.post("/api/bodies", json={
        "v": PROTOCOL,
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
        "v": PROTOCOL,
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
        "v": PROTOCOL,
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
        "v": PROTOCOL,
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
    answer = await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input", "output"], "about": "",
        "key": "voice@mac", "host": "mac"}, headers=auth)
    voice = {"X-Heare-Token": (await answer.json())["token"]}

    await client.post("/api/input", json={
        "kind": "state", "channel": "voice",
        "values": {"speaking": True}}, headers=voice)
    assert out.speaking is True

    await client.post("/api/input", json={
        "kind": "state", "channel": "voice",
        "values": {"speaking": False}}, headers=voice)
    assert out.speaking is False
    await _close(room, client)


async def test_a_part_declares_the_controls_the_person_will_see(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    ui = [
        {"name": "mic", "kind": "toggle", "label": "Microphone", "value": True},
        {"name": "volume", "kind": "slider", "label": "Volume",
         "value": 1.0, "min": 0, "max": 1, "step": 0.05},
    ]
    await client.post("/api/bodies", json={
        "v": PROTOCOL,
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
    answer = await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input", "output"], "about": "мікрофон",
        "key": "voice@mac", "host": "mac",
        "ui": [{"name": "mic", "kind": "toggle", "label": "Microphone", "value": True}],
    }, headers=auth)
    voice = {"X-Heare-Token": (await answer.json())["token"]}
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
        headers=voice)
    assert reported.status == 200
    body = (await (await client.get("/api/state", headers=auth)).json()
            )["bodies"]["voice@mac"]
    assert body["values"] == {"mic": False}

    wrong = await client.post("/api/controls", json={
        "part": "voice@mac", "name": "warp", "value": 1}, headers=auth)
    assert wrong.status == 404
    await _close(room, client)


async def test_a_state_report_without_a_part_is_refused(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    refused = await client.post("/api/input", json={
        "kind": "state", "channel": "voice", "values": {"mic": True}},
        headers=auth)
    assert refused.status == 404
    await _close(room, client)


async def test_a_control_by_channel_is_not_a_part(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    refused = await client.post("/api/controls", json={
        "part": "voice", "name": "mic", "value": True}, headers=auth)
    assert refused.status == 404
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


async def test_a_phone_that_speaks_hears_its_own_replies(tmp_path):
    """5 November: only "screen" and "voice" had a mouth on the brain, so a
    phone's question was answered on the page. A part that says it outputs
    gets its own channel; the Mac's voice keeps its own."""
    room, client, auth, settings = await _open(tmp_path)
    room.core.add_output(VoiceOut(room.core))
    code = pair.make(Path(settings.pair_codes_file))
    machine = await (await client.post(
        "/api/pair", json={"code": code, "kind": "machine", "name": "pixel"})).json()
    knocked = await (await client.post("/api/bodies", json={
        "v": PROTOCOL, "channel": "phone", "forms": ["input", "output"], "about": "a phone",
        "key": "phone@pixel", "host": "pixel"},
        headers={"X-Heare-Token": machine["token"]})).json()
    assert knocked["approved"] and knocked["token"]
    events = room.core.state.subscribe()
    room.model.will_say("Привіт.")
    said = await client.post("/api/input", json={
        "kind": "heard", "channel": "phone", "text": "привіт"},
        headers={"X-Heare-Token": knocked["token"]})
    assert said.status == 200
    await room.settled()
    replies = set()
    while not events.empty():
        event = events.get_nowait()
        if event.kind == "reply":
            replies.add(event.data["channel"])
    assert replies == {"phone"}
    assert isinstance(room.core.outputs["voice"], VoiceOut)
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


async def test_health_is_open_and_says_nothing_else(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    answer = await client.get("/health")
    assert answer.status == 200 and await answer.json() == {"ok": True}
    await _close(room, client)


def test_a_window_is_the_owner_only_when_the_server_says_so(monkeypatch):
    from heare.api import server

    monkeypatch.delenv("HEARE_WINDOWS_ARE_THE_OWNER", raising=False)
    assert not server.WINDOWS_ARE_THE_OWNER()
    monkeypatch.setenv("HEARE_WINDOWS_ARE_THE_OWNER", "1")
    assert server.WINDOWS_ARE_THE_OWNER()


# Every route a remote window may not use, named here on purpose. The census
# below walks the router, so a route added later has to be put in this table
# or in REMOTE_WINDOW_ALLOWED before the suite passes: the default is local.
LOCAL_ONLY = frozenset({
    ("POST", "/api/pair-code"),
    ("POST", "/api/keys/ask"),
    ("GET", "/api/keys/asked"),
    ("POST", "/api/keys/answer"),
    ("POST", "/api/update/machine"),
    ("POST", "/api/machine/mine/report"),
    ("GET", "/api/machines"),
    ("PUT", "/api/machines/{name}"),
    ("POST", "/api/machines/{name}/forget"),
    ("POST", "/api/machines/{name}/parts/{part}"),
    ("POST", "/api/machines/{name}/parts/{part}/restart"),
    ("GET", "/api/update/mine"),
    ("POST", "/api/update/mine/failed"),
    ("POST", "/api/update/mine/progress"),
    ("POST", "/api/bodies/{id}/approve"),
    ("POST", "/api/bodies/{id}/reject"),
    ("POST", "/api/bodies/{id}/forget"),
    ("POST", "/api/executor/claim"),
    ("POST", "/api/executor/{id}/step"),
    ("POST", "/api/executor/{id}/permit"),
    ("POST", "/api/executor/{id}/ask"),
    ("POST", "/api/executor/{id}/result"),
    ("POST", "/api/canvas/put"),
    ("POST", "/api/canvas/remove"),
    ("POST", "/api/controls"),
    ("GET", "/api/skills"),
    ("GET", "/api/prompt"),
    ("GET", "/api/context"),
    ("GET", "/api/sessions"),
    ("POST", "/api/sessions"),
    ("POST", "/api/sessions/{id}/open"),
    ("POST", "/api/sessions/{id}/rename"),
    ("DELETE", "/api/sessions/{id}"),
    ("GET", "/api/sessions/{id}/turns"),
    ("POST", "/api/sessions/{id}/files"),
    ("GET", "/api/sessions/{id}/files/{file}"),
    ("POST", "/api/skills/call"),
    ("POST", "/api/skills/{id}"),
    ("POST", "/api/tasks"),
    ("POST", "/api/settings"),
    ("GET", "/api/keys"),
    ("POST", "/api/keys"),
    ("POST", "/api/keys/adopt"),
    ("POST", "/api/features/{id}/act"),
    ("GET", "/api/update"),
    ("POST", "/api/update/check"),
    ("POST", "/api/update/install"),
    ("POST", "/api/update/failed"),
    ("GET", "/api/backups"),
    ("POST", "/api/backups"),
    ("POST", "/api/backups/bring"),
    ("GET", "/api/backups/{name}"),
    ("POST", "/api/backups/{name}/restore"),
    ("GET", "/api/mind/sources"),
    ("POST", "/api/mind/sources/{name}/import"),
    ("POST", "/api/mind/sources/{name}/undo"),
    ("POST", "/api/mind/facts/{id}/confirm"),
    ("POST", "/api/mind/facts/{id}/reject"),
    ("POST", "/api/mind/reminders/{id}/cancel"),
    ("GET", "/api/screens"),
    ("PUT", "/api/screens/{name}"),
    ("GET", "/screens/{name}"),
})


def _may_reach_remote(method: str, canonical: str) -> bool:
    """Whether a route's example path is allowed: a placeholder may be a
    number or a slug, so both are tried."""
    return any(
        api_server._remote_window_may(
            method, re.sub(r"\{[^}]+\}", filler, canonical))
        for filler in ("1", "x"))


def test_a_remote_window_keeps_the_pult_and_not_the_powers():
    allowed = [
        ("POST", "/api/bodies"),
        ("POST", "/api/message"),
        ("POST", "/api/input"),
        ("POST", "/api/input/said_over"),
        ("GET", "/api/events"),
        ("GET", "/api/state"),
        ("GET", "/api/history"),
        ("GET", "/api/canvas"),
        ("GET", "/api/system"),
        ("GET", "/api/tasks"),
        ("GET", "/api/controls"),
        ("GET", "/api/settings"),
        ("GET", "/api/models"),
        ("GET", "/api/memory"),
        ("GET", "/api/mind"),
        ("GET", "/api/mind/journal"),
        ("GET", "/api/mind/search"),
        ("GET", "/api/heard"),
        ("GET", "/api/heard/turn-1.wav"),
        ("GET", "/api/features"),
        ("GET", "/api/intents"),
        ("POST", "/api/intents/12/now"),
        ("POST", "/api/intents/12/drop"),
        ("POST", "/api/stop"),
        ("POST", "/api/tasks/12/stop"),
        ("POST", "/api/permission/12"),
        ("POST", "/api/answer/12"),
        ("POST", "/api/mind/journal/forget"),
        ("PUT", "/api/mind/profile"),
        ("POST", "/api/heard/turn-1"),
    ]
    denied = [
        ("POST", "/api/settings"),
        ("GET", "/api/keys"),
        ("POST", "/api/keys"),
        ("POST", "/api/pair-code"),
        ("POST", "/api/bodies/voice@mac/approve"),
        ("POST", "/api/bodies/voice@mac/reject"),
        ("POST", "/api/bodies/voice@mac/forget"),
        ("POST", "/api/executor/claim"),
        ("POST", "/api/executor/1/step"),
        ("POST", "/api/skills/call"),
        ("POST", "/api/tasks"),
        ("POST", "/api/controls"),
        ("POST", "/api/canvas/put"),
        ("POST", "/api/features/x/act"),
        ("GET", "/api/update"),
        ("POST", "/api/update/install"),
        ("GET", "/api/backups"),
        ("PUT", "/api/screens/today"),
    ]
    assert all(api_server._remote_window_may(method, path)
               for method, path in allowed)
    assert not any(api_server._remote_window_may(method, path)
                   for method, path in denied)
    # A HEAD is the GET it stands in for, never a way around the table.
    assert api_server._remote_window_may("HEAD", "/api/state")
    assert not api_server._remote_window_may("HEAD", "/api/keys")


async def test_every_route_is_decided_for_a_remote_window(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    undecided = []
    for route in client.app.router.routes():
        if route.method is None or route.resource is None:
            continue
        method = "GET" if route.method == "HEAD" else route.method
        canonical = route.resource.canonical
        if not (canonical.startswith("/api/")
                or canonical.startswith("/screens/")):
            continue  # the page and its fonts are public by design
        if (method, canonical) == ("POST", "/api/pair"):
            continue  # the one door a stranger may knock on
        if _may_reach_remote(method, canonical):
            continue
        if (method, canonical) not in LOCAL_ONLY:
            undecided.append((method, canonical))
    await _close(room, client)
    assert not undecided, (
        "a route is neither allowed to a remote window nor marked local-only: "
        f"{undecided}")


async def test_a_remote_window_cannot_reach_the_powers_that_change_the_mac(
        tmp_path, monkeypatch):
    monkeypatch.setattr(web.Request, "remote",
                        property(lambda self: "203.0.113.7"))
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    assert (await client.post(
        "/api/pair", json={"code": code, "kind": "window",
                           "name": "phone"})).status == 200
    assert (await client.get("/api/state")).status == 200
    assert (await client.get("/api/history")).status == 200
    assert (await client.get("/api/settings")).status == 200
    assert (await client.get("/api/controls")).status == 200
    assert (await client.post(
        "/api/message", json={"text": "привіт"})).status == 200
    await room.settled()
    assert (await client.post("/api/skills/call", json={
        "part": "voice@mac", "name": "x", "args": {}})).status == 403
    assert (await client.post("/api/tasks", json={"text": "щось"})).status == 403
    assert (await client.post(
        "/api/bodies/voice@mac/approve")).status == 403
    assert (await client.post("/api/executor/claim", json={})).status == 403
    assert (await client.get("/api/keys")).status == 403
    assert (await client.post("/api/settings", json={
        "deepseek_model": "deepseek-chat"})).status == 403
    assert (await client.post("/api/pair-code")).status == 403
    await _close(room, client)


async def test_a_remote_window_can_knock_as_its_own_body(tmp_path, monkeypatch):
    monkeypatch.setattr(web.Request, "remote",
                        property(lambda self: "203.0.113.7"))
    room, client, auth, settings = await _open(tmp_path)
    paired = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "phone"})
    assert paired.status == 200
    pid = (await paired.json())["pid"]

    # The page knocks without a key: its cookie is the window. A `key` in
    # the body names another part only for a machine; a window is always
    # itself, so the knock lands on its own card and cannot borrow one.
    answer = await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "web", "forms": ["input", "output"],
        "host": "elsewhere", "key": "voice@mac"})
    assert answer.status == 200
    given = await answer.json()
    assert given["approved"] is True and given["pid"] == pid

    bodies = (await (await client.get(
        "/api/state", headers=auth)).json())["bodies"]
    assert bodies[pid]["channel"] == "web"
    assert bodies[pid]["state"] == "approved"
    assert "voice@mac" not in bodies
    await _close(room, client)


async def test_the_allowlist_entries_all_match_a_live_route(tmp_path):
    room, client, _, _ = await _open(tmp_path)
    live = set()
    for route in client.app.router.routes():
        if route.method is None or route.resource is None:
            continue
        method = "GET" if route.method == "HEAD" else route.method
        live.add((method, route.resource.canonical))
    await _close(room, client)

    stale = []
    for method, rx in api_server.REMOTE_WINDOW_ALLOWED:
        if not any(
            method == live_method
            and rx.fullmatch(re.sub(r"\{[^}]+\}", filler, canonical))
            for live_method, canonical in live
            for filler in ("1", "x")
        ):
            stale.append((method, rx.pattern))
    assert not stale, f"allowlist entries match no live route: {stale}"


async def test_a_window_on_this_machine_keeps_the_person_s_powers(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    assert (await client.post(
        "/api/pair", json={"code": code, "kind": "window",
                           "name": "here"})).status == 200
    changed = await client.post("/api/settings", json={
        "deepseek_model": "deepseek-chat"})
    assert changed.status == 200
    assert (await client.get("/api/keys")).status == 200
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
        "v": PROTOCOL,
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
        json={"v": PROTOCOL, "channel": "voice", "host": "mac", "forms": ["input"]})
    assert knock.status == 200
    answer = await knock.json()
    assert answer["approved"] is True and answer["token"]
    # Back with its own key, as after the brain restarted: still trusted,
    # no question waits on the page.
    again = await (await client.post("/api/bodies", headers={
        "X-Heare-Token": answer["token"]},
        json={"v": PROTOCOL, "channel": "voice", "host": "mac", "forms": ["input"]})).json()
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
    knock = {"v": PROTOCOL, "channel": "web", "forms": ["input", "output"],
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
    knock = {"v": PROTOCOL, "channel": "web", "forms": ["input", "output"]}
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
    knock = {"v": PROTOCOL, "channel": "web", "forms": ["input", "output"],
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
    knock = {"v": PROTOCOL, "channel": "web", "forms": ["input", "output"]}
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
        "v": PROTOCOL,
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


async def _reading(stream, kinds: set[str]) -> list[dict]:
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
    return [e for e in got if e["kind"] in kinds]


async def _heard(stream, room, *events) -> list[dict]:
    for kind, data in events:
        room.core.state.emit(kind, **data)
    return await _reading(stream, {k for k, _ in events})


async def test_a_part_hears_its_own_channel_not_the_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, headers, _ = await _open(tmp_path)
    _, stream = await _part_listening(client, headers, {
        "v": PROTOCOL,
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
        "v": PROTOCOL,
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


async def test_a_part_that_speaks_no_protocol_version_is_told_why(tmp_path):
    room, client, headers, _ = await _open(tmp_path)
    answer = await (await client.post("/api/bodies", headers=headers, json={
        "channel": "eyes", "key": "eyes@mac"})).json()
    assert answer["approved"] is False
    assert "no protocol version" in answer["why"]
    await _close(room, client)


async def test_the_brain_says_its_version_and_a_part_s_is_kept(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, auth, _ = await _open(tmp_path)
    await client.post("/api/bodies", json={
        "v": PROTOCOL,
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
    assert listed["groups"] == [{"id": "memory", "title": "Memory",
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
    assert listed["purpose"] == "collect" and listed["measured"] is False
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


async def test_a_backup_is_made_listed_taken_brought_and_restored(tmp_path, monkeypatch):
    import os
    import signal

    from heare.api import server

    room, client, headers, settings = await _open(tmp_path)
    home = tmp_path / "home"
    (home / "memory" / "journal").mkdir(parents=True)
    (home / "memory" / "journal" / "2026-10-25.jsonl").write_text("{}\n", encoding="utf-8")
    (home / "settings").mkdir()
    (home / "settings" / "config.toml").write_text("", encoding="utf-8")

    made = await client.post("/api/backups", headers=headers, json={"sound": False})
    assert made.status == 200
    entry = await made.json()
    assert entry["sound"] is False and entry["counts"]["days"] == 1
    listed = await (await client.get("/api/backups", headers=headers)).json()
    assert [b["name"] for b in listed["backups"]] == [entry["name"]]
    assert listed["restored"] is None

    taken = await client.get(f"/api/backups/{entry['name']}", headers=headers)
    assert taken.status == 200
    assert "attachment" in taken.headers["Content-Disposition"]
    data = await taken.read()
    assert data[:2] == b"PK"
    assert (await client.get("/api/backups/..%2Fmachine%2Fapi_token",
                             headers=headers)).status == 404
    assert (await client.get("/api/backups/heare-20990101-000000.zip",
                             headers=headers)).status == 404

    brought = await client.post("/api/backups/bring", headers=headers, data=data)
    assert brought.status == 200
    assert (await brought.json())["name"] != entry["name"]
    junk = await client.post("/api/backups/bring", headers=headers, data=b"not a zip")
    assert junk.status == 400
    assert len((await (await client.get("/api/backups", headers=headers)).json())["backups"]) == 2
    assert not list((home / "backups").glob(".brought-*"))

    killed = []
    monkeypatch.setattr(server.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    restore = await client.post(f"/api/backups/{entry['name']}/restore", headers=headers)
    assert restore.status == 200
    assert (home / "restore-pending" / "memory" / "journal" / "2026-10-25.jsonl").exists()
    await asyncio.sleep(0.7)
    assert killed == [(os.getpid(), signal.SIGTERM)]

    assert (await client.get("/api/backups")).status == 401
    assert (await client.post("/api/backups/bring", data=data)).status == 401
    await _close(room, client)


async def test_the_update_door_checks_and_refuses_what_it_cannot_do(tmp_path, monkeypatch):
    monkeypatch.delenv("HEARE_PARENT_PID", raising=False)
    room, client, headers, settings = await _open(tmp_path)
    settings.set("update_feed", "http://127.0.0.1:9/latest")

    state = await (await client.get("/api/state", headers=headers)).json()
    assert state["update"]["state"] == "idle" and state["update"]["can_install"] is False
    checked = await (await client.post("/api/update/check", headers=headers)).json()
    assert checked["error"]
    refused = await client.post("/api/update/install", headers=headers)
    assert refused.status == 409 and "нової версії немає" in await refused.text()
    failed = await client.post("/api/update/failed", headers=headers, json={"why": "не записувана"})
    assert (await failed.json())["state"] == "failed"
    assert (await client.get("/api/update")).status == 401
    await _close(room, client)


async def _knock(client, headers, pid, forms, *, path="/api/bodies", **more):
    channel, host = pid.split("@")
    answer = await client.post(path, json={
        "v": PROTOCOL,
        "channel": channel, "forms": forms, "about": "", "key": pid, "host": host,
        **more}, headers=headers)
    return {"X-Heare-Token": (await answer.json())["token"]}


async def test_the_old_knock_path_is_the_same_door(tmp_path):
    (tmp_path / "parts.json").write_text(
        '["old@desk", "new@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    old = await _knock(client, master, "old@desk", ["output"],
                       path="/api/bodies")
    new = await _knock(client, master, "new@desk", ["output"],
                       path="/api/parts")
    assert old["X-Heare-Token"] and new["X-Heare-Token"]
    state = await (await client.get("/api/state", headers=master)).json()
    assert state["bodies"]["old@desk"]["state"] == "approved"
    assert state["bodies"]["new@desk"]["state"] == "approved"
    await _close(room, client)


PIN = {"name": "pin3", "about": "реле", "effect": "change",
       "params": {"type": "object", "properties": {"on": {"type": "boolean"}}}}


async def test_only_a_let_in_executor_may_call_the_parts_skills(tmp_path):
    (tmp_path / "parts.json").write_text('["native@desk", "oled@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    led = {"name": "led", "about": "світло", "effect": "harmless",
           "params": {"type": "object", "properties": {}}}
    panel = await _knock(client, master, "oled@desk", ["output"], name="desk", skills=[led])
    hand = await _knock(client, master, "native@desk", ["executor"])

    offered = await (await client.get("/api/skills", headers=hand)).json()
    unknown = await client.post("/api/skills/call", json={
        "part": "oled@desk", "name": "pin9", "args": {}}, headers=hand)

    assert offered["oled@desk"]["name"] == "desk"
    assert [s["name"] for s in offered["oled@desk"]["skills"]] == ["led"]
    assert unknown.status == 404
    assert (await client.get("/api/skills", headers=panel)).status == 403
    await _close(room, client)


async def test_a_call_waits_for_the_part_s_own_answer(tmp_path):
    (tmp_path / "parts.json").write_text('["native@desk", "oled@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    panel = await _knock(client, master, "oled@desk", ["output"], skills=[PIN])
    hand = await _knock(client, master, "native@desk", ["executor"])
    events = room.core.state.subscribe()

    calling = asyncio.create_task(client.post("/api/skills/call", json={
        "part": "oled@desk", "name": "pin3", "args": {"on": True}}, headers=hand))
    event = None
    while event is None or event.kind != "call":
        event = await asyncio.wait_for(events.get(), 2)
    assert event.data["args"] == {"on": True}
    await client.post(f"/api/skills/{event.data['id']}", json={"ok": True, "result": "pin3: 1"},
                      headers=hand)
    stranger = await client.post(f"/api/skills/{event.data['id']}", json={"ok": True},
                                 headers=hand)
    await client.post(f"/api/skills/{event.data['id']}", json={"ok": True, "result": "pin3: 1"},
                      headers=panel)

    assert stranger.status == 404
    assert (await (await calling).json()) == {"ok": True, "result": "pin3: 1"}
    await _close(room, client)


async def test_a_part_with_a_new_switch_is_asked_about_again(tmp_path):
    (tmp_path / "parts.json").write_text('["oled@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    await _knock(client, master, "oled@desk", ["output"], skills=[PIN])

    knocking = asyncio.create_task(client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "oled", "forms": ["output"], "about": "", "key": "oled@desk", "host": "desk",
        "skills": [PIN, {**PIN, "name": "pin4"}]}, headers=master))
    for _ in range(100):
        state = await (await client.get("/api/state", headers=master)).json()
        if state["bodies"]["oled@desk"]["state"] == "pending":
            break
        await asyncio.sleep(0.01)
    assert state["bodies"]["oled@desk"]["state"] == "pending"
    await client.post("/api/bodies/oled@desk/approve", headers=master)
    assert (await (await knocking).json())["approved"] is True
    await _close(room, client)



async def test_a_knock_is_told_the_brain_s_offset_from_utc(tmp_path):
    import time as clock

    (tmp_path / "parts.json").write_text('["oled@desk"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    answer = await (await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "oled", "forms": ["output"], "about": "", "key": "oled@desk",
        "host": "desk"}, headers=master)).json()
    assert answer["utc_offset"] == clock.localtime().tm_gmtoff
    await _close(room, client)


async def test_a_machine_key_cannot_claim_another_parts_name(tmp_path):
    from heare.reach.remote import RemoteExecutor

    (tmp_path / "parts.json").write_text('["opencode@mac"]', encoding="utf-8")
    room, client, master, settings = await _open(
        tmp_path,
        make_executor=lambda i, n, a, m: RemoteExecutor(i, n, a, m, claim_wait_s=0.5))
    agent = await _knock(client, master, "opencode@mac", ["executor"])
    executor = room.core.executors["opencode@mac"]

    given = await (await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "machine", "name": "mac"})).json()
    stolen = await client.post("/api/bodies", headers={
        "X-Heare-Token": given["token"]}, json={
        "v": PROTOCOL,
        "channel": "opencode", "forms": ["executor"], "about": "mine",
        "key": "opencode@mac", "host": "mac"})

    assert stolen.status == 403
    assert "not your name" in await stolen.text()
    assert room.core.executors["opencode@mac"] is executor
    state = await (await client.get("/api/state", headers=master)).json()
    assert state["bodies"]["opencode@mac"]["state"] == "approved"
    assert state["bodies"]["opencode@mac"]["about"] == ""
    # The part's own key was not replaced by the refused claim.
    assert (await client.get("/api/skills", headers=agent)).status == 200
    await _close(room, client)


async def test_a_machine_key_can_claim_its_own_untouched_pid(tmp_path):
    room, client, master, settings = await _open(tmp_path)
    given = await (await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "machine", "name": "mac"})).json()
    machine = {"X-Heare-Token": given["token"]}

    first = await client.post("/api/bodies", headers=machine, json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input"], "host": "mac", "key": "voice@mac"})
    answer = await first.json()
    assert first.status == 200 and answer["approved"] is True and answer["token"]
    assert authlib.owner(Path(settings.part_tokens_file), answer["token"]) == "voice@mac"

    # The machine may knock again for the pid it claimed itself.
    again = await client.post("/api/bodies", headers=machine, json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input"], "host": "mac", "key": "voice@mac"})
    assert (await again.json())["approved"] is True

    # And for another pid nobody has claimed.
    other = await client.post("/api/bodies", headers=machine, json={
        "v": PROTOCOL,
        "channel": "eyes", "forms": ["input"], "host": "mac", "key": "eyes@mac"})
    other_answer = await other.json()
    assert other_answer["approved"] is True and other_answer["token"]
    await _close(room, client)


async def test_an_approved_part_cannot_widen_hears_on_a_knock(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    old = await _knock(client, master, "voice@mac", ["input"])

    knocking = asyncio.create_task(client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input"], "about": "", "key": "voice@mac",
        "host": "mac", "hears": "all"}, headers=old))
    for _ in range(100):
        state = await (await client.get("/api/state", headers=master)).json()
        if state["bodies"]["voice@mac"]["state"] == "pending":
            break
        await asyncio.sleep(0.01)
    assert state["bodies"]["voice@mac"]["state"] == "pending"
    # The key the part had while it was trusted no longer opens the door.
    assert (await client.post("/api/input", json={
        "kind": "started", "channel": "voice"}, headers=old)).status == 401

    await client.post("/api/bodies/voice@mac/approve", headers=master)
    granted = await (await knocking).json()
    assert granted["approved"] is True and granted["token"]
    assert (await client.post("/api/input", json={
        "kind": "started", "channel": "voice"},
        headers={"X-Heare-Token": granted["token"]})).status == 200
    await _close(room, client)


async def test_a_rejected_part_keeps_no_key(tmp_path):
    (tmp_path / "parts.json").write_text('["voice@mac"]', encoding="utf-8")
    room, client, master, _ = await _open(tmp_path)
    old = await _knock(client, master, "voice@mac", ["input"])

    knocking = asyncio.create_task(client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["input"], "about": "", "key": "voice@mac",
        "host": "mac", "hears": "all"}, headers=old))
    for _ in range(100):
        state = await (await client.get("/api/state", headers=master)).json()
        if state["bodies"]["voice@mac"]["state"] == "pending":
            break
        await asyncio.sleep(0.01)
    assert state["bodies"]["voice@mac"]["state"] == "pending"
    await client.post("/api/bodies/voice@mac/reject", headers=master)
    assert (await (await knocking).json())["approved"] is False
    assert (await client.post("/api/input", json={
        "kind": "started", "channel": "voice"}, headers=old)).status == 401
    await _close(room, client)


async def test_a_window_cannot_point_the_brain_at_another_update_feed(tmp_path):
    room, client, master, settings = await _open(tmp_path)
    paired = await client.post("/api/pair", json={
        "code": pair.make(Path(settings.pair_codes_file)),
        "kind": "window", "name": "phone"})
    window = {"X-Heare-Token": paired.cookies["heare_session"].value}

    # A window on this machine is the pult: it keeps the person's powers.
    assert (await client.post("/api/settings", json={"language": "en"},
                              headers=window)).status == 200
    # From another machine the pult stays and the changing powers are shut.
    for method, path in (
        ("POST", "/api/settings"), ("GET", "/api/keys"), ("POST", "/api/keys"),
        ("POST", "/api/keys/adopt"), ("GET", "/api/update"),
        ("POST", "/api/update/install"), ("GET", "/api/backups"),
        ("POST", "/api/backups"), ("POST", "/api/backups/x/restore"),
        ("POST", "/api/pair-code"), ("PUT", "/api/screens/home"),
        ("POST", "/api/skills/call"),
        ("POST", "/api/executor/claim"), ("POST", "/api/tasks"),
    ):
        assert not api_server._remote_window_may(method, path), (method, path)
    for method, path in (
        ("POST", "/api/bodies"),
        ("POST", "/api/message"), ("GET", "/api/state"), ("GET", "/api/events"),
        ("POST", "/api/input"),
    ):
        assert api_server._remote_window_may(method, path), (method, path)
    assert api_server._loopback("127.0.0.1")
    assert not api_server._loopback("192.168.1.50")
    await _close(room, client)


async def test_a_second_knock_keeps_the_executor_and_its_job(tmp_path):
    from heare.reach.remote import RemoteExecutor

    (tmp_path / "parts.json").write_text('["opencode@mac"]', encoding="utf-8")
    room, client, master, _ = await _open(
        tmp_path,
        make_executor=lambda i, n, a, m: RemoteExecutor(i, n, a, m, claim_wait_s=1.0))
    hands = await _knock(client, master, "opencode@mac", ["executor"])
    executor = room.core.executors["opencode@mac"]

    task = room.core.delegate("зроби щось")
    claim = await client.post("/api/executor/claim", json={},
                              headers=hands)
    job = (await claim.json())["id"]

    again = await client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "opencode", "forms": ["executor"], "about": "код, оновлено",
        "key": "opencode@mac", "host": "mac"}, headers=hands)
    assert (await again.json())["approved"] is True
    assert room.core.executors["opencode@mac"] is executor

    answered = await client.post(f"/api/executor/{job}/result",
                                 json={"status": "done", "result": "зроблено"},
                                 headers=hands)
    assert answered.status == 200 and (await answered.json())["ok"] is True
    await room.until(lambda: task.status == "done")
    assert task.result == "зроблено"
    await _close(room, client)


async def test_a_claim_without_a_part_key_is_refused(tmp_path):
    room, client, master, _ = await _open(tmp_path)
    refused = await client.post("/api/executor/claim", json={}, headers=master)
    assert refused.status == 404
    await _close(room, client)


async def test_two_machines_may_each_bring_hands_of_one_channel(tmp_path):
    from heare.reach.remote import RemoteExecutor

    room, client, master, settings = await _open(
        tmp_path,
        make_executor=lambda i, n, a, m: RemoteExecutor(i, n, a, m, claim_wait_s=0.5))
    for name in ("server", "mac"):
        machine = (await (await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "machine", "name": name})).json())["token"]
        granted = await _knock(
            client, {"X-Heare-Token": machine}, f"native-hands@{name}", ["executor"])
        assert granted["X-Heare-Token"]
    bodies = (await (await client.get("/api/state", headers=master)).json())["bodies"]
    assert bodies["native-hands@server"]["state"] == "approved"
    assert bodies["native-hands@mac"]["state"] == "approved"
    assert {"native-hands@server", "native-hands@mac"} <= set(room.core.executors)
    await _close(room, client)


async def test_each_hand_hears_only_what_is_addressed_to_it(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "HEARTBEAT_S", 0.05)
    room, client, master, settings = await _open(tmp_path)
    tokens = {}
    for name in ("server", "mac"):
        machine = (await (await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "machine", "name": name})).json())["token"]
        tokens[name] = await _knock(
            client, {"X-Heare-Token": machine},
            f"native-hands@{name}", ["executor"])
    streams = {}
    for name in ("server", "mac"):
        streams[name] = await client.get("/api/events", headers=tokens[name])
        await streams[name].content.readany()

    room.core.state.emit("control", channel="native-hands", name="work",
                         value="x", part="native-hands@server")
    room.core.state.emit("control", channel="native-hands", name="other",
                         value="y")

    server = await _reading(streams["server"], {"control"})
    mac = await _reading(streams["mac"], {"control"})
    assert [e["name"] for e in server] == ["work"]
    assert mac == []

    streams["server"].close()
    streams["mac"].close()
    await _close(room, client)


async def test_a_twins_state_lands_on_its_own_card(tmp_path):
    room, client, master, settings = await _open(tmp_path)
    tokens = {}
    for name in ("server", "mac"):
        machine = (await (await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "machine", "name": name})).json())["token"]
        tokens[name] = await _knock(
            client, {"X-Heare-Token": machine},
            f"native-hands@{name}", ["executor"])

    answered = await client.post("/api/input", json={
        "kind": "state", "channel": "native-hands", "values": {"work": "x"}},
        headers=tokens["server"])
    assert answered.status == 200

    bodies = (await (await client.get("/api/state", headers=master)).json())["bodies"]
    assert bodies["native-hands@server"]["values"]["work"] == "x"
    assert "work" not in bodies["native-hands@mac"]["values"]
    await _close(room, client)


async def test_a_second_output_on_a_channel_is_still_refused(tmp_path):
    room, client, master, settings = await _open(tmp_path)
    tokens = {}
    for name in ("server", "mac"):
        tokens[name] = (await (await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "machine", "name": name})).json())["token"]
    first = await client.post("/api/bodies", headers={
        "X-Heare-Token": tokens["server"]}, json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["output"], "host": "server",
        "key": "voice@server"})
    assert (await first.json())["approved"] is True
    second = await client.post("/api/bodies", headers={
        "X-Heare-Token": tokens["mac"]}, json={
        "v": PROTOCOL,
        "channel": "voice", "forms": ["output"], "host": "mac",
        "key": "voice@mac"})
    assert second.status == 403
    assert "not your name" in await second.text()
    await _close(room, client)


async def test_a_hand_that_loses_its_agent_is_no_longer_an_executor(tmp_path):
    from heare.reach.remote import RemoteExecutor

    (tmp_path / "parts.json").write_text('["opencode@mac"]', encoding="utf-8")
    room, client, master, _ = await _open(
        tmp_path,
        make_executor=lambda i, n, a, m: RemoteExecutor(i, n, a, m, claim_wait_s=0.5))
    hands = await _knock(client, master, "opencode@mac", ["executor"])
    assert "opencode@mac" in room.core.executors

    clean = asyncio.create_task(client.post("/api/bodies", json={
        "v": PROTOCOL,
        "channel": "opencode", "forms": [], "about": "", "key": "opencode@mac",
        "host": "mac"}, headers=hands))
    for _ in range(100):
        state = await (await client.get("/api/state", headers=master)).json()
        if state["bodies"]["opencode@mac"]["state"] == "pending":
            break
        await asyncio.sleep(0.01)
    assert state["bodies"]["opencode@mac"]["state"] == "pending"
    await client.post("/api/bodies/opencode@mac/approve", headers=master)
    assert (await (await clean).json())["approved"] is True
    assert "opencode@mac" not in room.core.executors
    await _close(room, client)


async def test_forgetting_one_hand_leaves_its_twin_alone(tmp_path):
    from heare.reach.remote import RemoteExecutor

    room, client, master, settings = await _open(
        tmp_path,
        make_executor=lambda i, n, a, m: RemoteExecutor(i, n, a, m, claim_wait_s=0.5))
    for name in ("server", "mac"):
        machine = (await (await client.post("/api/pair", json={
            "code": pair.make(Path(settings.pair_codes_file)),
            "kind": "machine", "name": name})).json())["token"]
        await _knock(client, {"X-Heare-Token": machine},
                     f"native-hands@{name}", ["executor"])
    assert {"native-hands@server", "native-hands@mac"} <= set(room.core.executors)

    forgotten = await client.post("/api/bodies/native-hands@server/forget",
                                  headers=master)
    assert forgotten.status == 200
    assert "native-hands@server" not in room.core.executors
    assert "native-hands@mac" in room.core.executors
    bodies = (await (await client.get("/api/state", headers=master)).json())["bodies"]
    assert "native-hands@server" not in bodies
    assert bodies["native-hands@mac"]["state"] == "approved"
    await _close(room, client)


async def test_forgetting_a_hand_ends_its_work(tmp_path):
    from heare.core.i18n import _ as said_as
    from heare.reach.remote import RemoteExecutor

    (tmp_path / "parts.json").write_text('["opencode@mac"]', encoding="utf-8")
    room, client, master, _ = await _open(
        tmp_path,
        make_executor=lambda i, n, a, m: RemoteExecutor(i, n, a, m, claim_wait_s=0.5))
    await _knock(client, master, "opencode@mac", ["executor"])
    hands = room.core.executors["opencode@mac"]

    task = room.core.delegate("зроби щось")
    await room.until(lambda: bool(hands._jobs))

    forgotten = await client.post("/api/bodies/opencode@mac/forget", headers=master)

    assert forgotten.status == 200
    await room.until(lambda: task.status == "failed")
    assert task.result == said_as(
        "The hands on {where} are not connected.").format(where="mac")
    assert "opencode@mac" not in room.core.executors
    await _close(room, client)


async def test_a_result_for_an_unknown_job_is_refused_and_logged(tmp_path, caplog):
    from heare.reach.remote import RemoteExecutor

    room, client, _, settings = await _open(tmp_path)
    room.core.add_executor(RemoteExecutor("worker@bench", "worker", "робить усе", "bench"))
    token = authlib.issue(Path(settings.part_tokens_file), "worker@bench")

    with caplog.at_level(logging.WARNING, logger="heare.api.server"):
        refused = await client.post(
            "/api/executor/999/result",
            json={"status": "done", "result": "нікому"},
            headers={"X-Heare-Token": token})

    assert refused.status == 404
    assert any("unknown job 999" in record.message for record in caplog.records)
    await _close(room, client)


async def test_a_paired_machine_never_vouches_for_a_key(tmp_path):
    from heare import keys

    room, client, auth, settings = await _open(tmp_path)
    keys.store("GROQ_API_KEY", "gsk_test")
    code = pair.make(Path(settings.pair_codes_file))
    machine = await (await client.post("/api/pair", json={
        "code": code, "kind": "machine", "name": "mac"})).json()
    head = {"X-Heare-Token": machine["token"]}
    knocked = await (await client.post("/api/bodies", headers=head, json={
        "v": PROTOCOL,
        "channel": "voice", "key": "voice@mac", "host": "mac"})).json()
    part = {"X-Heare-Token": knocked["token"]}
    ask = {"key": "GROQ_API_KEY", "fingerprint": "a", "version": "1"}
    assert (await client.post("/api/keys/ask", headers=head, json=ask)).status == 403
    first = await (await client.post("/api/keys/ask", headers=part, json=ask)).json()
    assert first == {"waiting": True}
    # A part cannot say yes to itself.
    assert (await client.post("/api/keys/answer", headers=part, json={
        "host": "mac", "allow": True})).status == 403
    assert (await client.post("/api/keys/answer", headers=auth, json={
        "host": "mac", "allow": True})).status == 200
    lent = await (await client.post("/api/keys/ask", headers=part, json=ask)).json()
    assert lent == {"value": "gsk_test"}
    await _close(room, client)


async def test_a_part_stays_its_machines_across_a_brain_restart(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    machine = await (await client.post("/api/pair", json={
        "code": code, "kind": "machine", "name": "mac"})).json()
    about = {"v": PROTOCOL, "channel": "voice", "key": "voice@mac", "host": "mac"}
    first = await (await client.post("/api/bodies", json=about, headers={
        "X-Heare-Token": machine["token"]})).json()
    await _close(room, client)

    room, client, auth, settings = await _open(tmp_path)
    assert (await client.post("/api/bodies", json=about, headers={
        "X-Heare-Token": first["token"]})).status == 200
    # The app restarts before the brain saw the old stream end.
    again = await client.post("/api/bodies", json=about, headers={
        "X-Heare-Token": machine["token"]})
    assert again.status == 200
    await _close(room, client)


async def test_a_machine_asks_only_for_its_own_update_and_nothing_else(tmp_path):
    room, client, auth, settings = await _open(tmp_path)
    code = pair.make(Path(settings.pair_codes_file))
    given = await (await client.post(
        "/api/pair", json={"code": code, "kind": "machine", "name": "mac"})).json()
    machine = {"X-Heare-Token": given["token"]}
    mine = await client.get("/api/update/mine?version=0.1.0&arch=arm64", headers=machine)
    assert mine.status == 200 and await mine.json() == {}
    # The app's number for the page; no ask, so nowhere to put it.
    said = await client.post("/api/update/mine/progress", headers=machine,
                             json={"received": 1, "total": 2})
    assert said.status == 200 and (await said.json()).get("machines") is None
    # The owner's button is not the machine's to press.
    pressed = await client.post("/api/update/machine", headers=machine, json={"machine": "machine@mac"})
    assert pressed.status == 403
    await _close(room, client)


async def test_what_the_engine_holds_is_shown_and_can_be_said_or_dropped(tmp_path):
    room, client, auth, _ = await _open(tmp_path)
    # The page is the only caller here: a running engine would deliver a held
    # answer on its own between the two requests, and the drop would be a 404.
    room._motor.cancel()
    store = room.extra["intents"]
    held = store.hold("Сайт готовий.", why="task_done", weight=2,
                      about="task:t", until=time.time() + 3600)
    listed = await (await client.get("/api/intents", headers=auth)).json()
    assert [(i["what"], i["why"], i["weight"]) for i in listed["intents"]] == [
        ("Сайт готовий.", "task_done", 2)]

    room.model.will_say("Сайт готовий.")
    said = await client.post(f"/api/intents/{held.id}/now", headers=auth)
    assert await said.json() == {"ok": True}
    await room.until(lambda: room.page.said)
    assert "Сайт готовий." in room.page.said[-1]

    other = store.hold("Онови залежності.", why="task_failed", weight=2,
                       about="task:t2", until=time.time() + 3600)
    dropped = await client.post(f"/api/intents/{other.id}/drop", headers=auth)
    assert await dropped.json() == {"ok": True}
    assert await (await client.get("/api/intents", headers=auth)).json() == {
        "intents": []}

    missing = await client.post("/api/intents/9999/drop", headers=auth)
    assert missing.status == 404
    await _close(room, client)


async def test_a_machine_reports_its_parts_and_learns_the_wish(tmp_path):
    room, client, owner, settings = await _open(tmp_path)
    token = authlib.issue(Path(settings.part_tokens_file), "machine@mac")
    machine = {"X-Heare-Token": token}
    body = {
        "name": "mac", "version": "0.9.10", "platform": "darwin",
        "load": [0.4, 0.5, 0.6], "disk_free": 32_000_000_000,
        "disk_total": 500_000_000_000,
        "parts": [{"name": "voice", "label": "Voice", "what": "listens and speaks",
                   "channel": "voice", "on": True, "state": "running", "why": ""}],
    }
    reported = await client.post("/api/machine/mine/report", headers=machine, json=body)
    assert reported.status == 200
    assert await reported.json() == {"desired": {}, "restart": []}

    listed = await client.get("/api/machines", headers=owner)
    machines = (await listed.json())["machines"]
    assert [one["name"] for one in machines] == ["mac"]
    assert machines[0]["state"] == "online"
    assert machines[0]["parts"][0]["label"] == "Voice"

    turned = await client.post("/api/machines/mac/parts/voice",
                               headers=owner, json={"on": False})
    assert turned.status == 200
    after = await turned.json()
    assert after["machines"][0]["parts"][0]["desired"] is False
    assert after["machines"][0]["parts"][0]["on"] is True, "the keeper has not said yet"

    again = await client.post("/api/machine/mine/report", headers=machine, json=body)
    assert await again.json() == {"desired": {"voice": False}, "restart": []}

    # A machine's key reports and nothing else: the list is the person's.
    denied = await client.get("/api/machines", headers=machine)
    assert denied.status == 403
    await _close(room, client)


async def test_the_brain_s_own_keeper_reports_with_the_master_key(tmp_path):
    room, client, owner, settings = await _open(tmp_path)
    body = {"name": "this-mac", "version": "0.9.10", "platform": "darwin",
            "parts": [{"name": "voice", "label": "Voice", "channel": "voice",
                       "on": True, "state": "running", "why": ""}]}
    answer = await client.post("/api/machine/mine/report", headers=owner, json=body)
    assert answer.status == 200

    listed = await client.get("/api/machines", headers=owner)
    machines = (await listed.json())["machines"]
    assert [one["name"] for one in machines] == ["this-mac"]
    await _close(room, client)


async def test_a_machine_can_be_renamed_restarted_and_forgotten(tmp_path):
    room, client, owner, settings = await _open(tmp_path)
    token = authlib.issue(Path(settings.part_tokens_file), "machine@mac")
    machine = {"X-Heare-Token": token}
    body = {
        "name": "mac", "version": "0.9.10", "platform": "darwin",
        "load": [0.4], "disk_free": 1, "disk_total": 2,
        "mem_free": 3, "mem_total": 4,
        "parts": [{"name": "voice", "label": "Voice", "channel": "voice",
                   "on": True, "state": "running", "why": ""}],
    }
    assert (await client.post("/api/machine/mine/report", headers=machine, json=body)).status == 200

    renamed = await client.put("/api/machines/mac", headers=owner, json={"alias": "Мак"})
    assert renamed.status == 200
    assert (await renamed.json())["machines"][0]["alias"] == "Мак"

    restarted = await client.post("/api/machines/mac/parts/voice/restart",
                                  headers=owner, json={})
    assert restarted.status == 200
    again = await client.post("/api/machine/mine/report", headers=machine, json=body)
    assert (await again.json())["restart"] == ["voice"]
    once = await client.post("/api/machine/mine/report", headers=machine, json=body)
    assert (await once.json())["restart"] == [], "a restart is a word, not a state"

    forgotten = await client.post("/api/machines/mac/forget", headers=owner, json={})
    assert forgotten.status == 200
    listed = await client.get("/api/machines", headers=owner)
    assert (await listed.json())["machines"] == []
    denied = await client.post("/api/machine/mine/report", headers=machine, json=body)
    assert denied.status == 401, "the key went with the machine"
    await _close(room, client)


async def test_a_paired_machine_with_no_parts_is_listed(tmp_path):
    room, client, owner, settings = await _open(tmp_path)
    authlib.issue(Path(settings.part_tokens_file), "machine@ghost")

    listed = await client.get("/api/machines", headers=owner)
    machines = (await listed.json())["machines"]
    assert [one["name"] for one in machines] == ["ghost"]
    assert machines[0]["reported"] is False and machines[0]["keyed"] is True
    await _close(room, client)


async def test_a_machine_name_is_kept_for_one_machine(tmp_path):
    """A name is a machine's key: pairing a second machine under it would
    replace the first one's key and stop its parts without a word."""
    room, client, owner, settings = await _open(tmp_path)

    def mint() -> str:
        return pair.make(Path(settings.pair_codes_file))

    first = await client.post("/api/pair", json={
        "code": mint(), "kind": "machine", "name": "mac"})
    assert first.status == 200

    code = mint()
    twice = await client.post("/api/pair", json={
        "code": code, "kind": "machine", "name": "mac"})
    assert twice.status == 409
    # The refused try took no code: another name works with the same one.
    other = await client.post("/api/pair", json={
        "code": code, "kind": "machine", "name": "mbp"})
    assert other.status == 200

    nameless = await client.post("/api/pair", json={"code": mint(), "kind": "machine"})
    assert nameless.status == 400, "a machine is called by a name, not by its address"

    forgotten = await client.post("/api/machines/mac/forget", headers=owner, json={})
    assert forgotten.status == 200
    again = await client.post("/api/pair", json={
        "code": mint(), "kind": "machine", "name": "mac"})
    assert again.status == 200, "forgetting a machine frees its name"
    await _close(room, client)


async def test_a_part_that_cannot_run_here_says_why_on_the_page(tmp_path):
    room, client, owner, settings = await _open(tmp_path)
    token = authlib.issue(Path(settings.part_tokens_file), "machine@mac")
    body = {"name": "mac", "parts": [
        {"name": "eyes", "label": "Eyes", "channel": "eyes", "on": False,
         "state": "off", "why": "", "why_not": "only on a Mac"}]}
    reported = await client.post("/api/machine/mine/report",
                                 headers={"X-Heare-Token": token}, json=body)
    assert reported.status == 200

    machines = (await (await client.get("/api/machines", headers=owner)).json())["machines"]
    part = machines[0]["parts"][0]
    assert part["why_not"] == "only on a Mac" and part["on"] is False
    await _close(room, client)
