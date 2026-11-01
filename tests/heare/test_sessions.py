"""Sessions, end to end through the door: a real Core, Book and server, a stand-in model."""

from __future__ import annotations

import asyncio
import json

import pytest

from heare.api.sessions import ATTACHED, TEXT_CHARS, Kept, Sessions, kind_of
from tests.heare.fakes import Says, Worker
from tests.heare.test_api import _close, _open as open_door


def told(model) -> str:
    return json.dumps(model.asked[-1], ensure_ascii=False)


async def listed(client, h):
    return await (await client.get("/api/sessions", headers=h)).json()


async def say(room, client, h, text, **more):
    asked = len(room.model.asked)
    assert (await client.post("/api/message", json={"text": text, **more}, headers=h)).status == 200
    await room.until(lambda: len(room.model.asked) > asked and room.core.state.phase == "idle")


async def test_a_new_session_starts_clean_and_the_old_one_comes_back(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    room.model.will_say(Says("Перша."), Says("Друга."), Says("Третя."))
    await say(room, client, h, "про панель")
    first = (await listed(client, h))["active"]

    assert (await client.post("/api/sessions", headers=h)).status == 200
    assert room.core.history == []
    await say(room, client, h, "про погоду")
    assert "про панель" not in told(room.model)

    assert (await client.post(f"/api/sessions/{first}/open", headers=h)).status == 200
    await say(room, client, h, "знову панель")
    assert "про панель" in told(room.model) and "про погоду" not in told(room.model)

    turns = await (await client.get(f"/api/sessions/{first}/turns", headers=h)).json()
    assert [t["text"] for t in turns][:2] == ["про панель", "Перша."]
    await _close(room, client)


async def test_a_session_survives_a_restart(tmp_path):
    root = tmp_path / "s"
    sessions = Sessions(root)
    session = sessions.new()
    sessions.add(session, "user", "слово")
    again = Sessions(root)
    assert again.active == session and again.for_model(session)[0]["content"] == "слово"


async def test_the_title_comes_from_the_first_words(tmp_path):
    sessions = Sessions(tmp_path)
    session = sessions.new()
    sessions.add(session, "assistant", "привіт")
    sessions.add(session, "user", "налаштуй   панель\n" + "x" * 99)
    assert sessions.listed()[0]["title"].startswith("налаштуй панель")
    assert len(sessions.listed()[0]["title"]) <= 48


async def test_the_talk_from_before_is_kept_as_earlier(tmp_path):
    sessions = Sessions(tmp_path)
    sessions.seed([{"role": "user", "content": "давнє"}])
    sessions.seed([{"role": "user", "content": "вдруге"}])
    assert len(sessions.listed()) == 1 and sessions.listed()[0]["earlier"]
    assert sessions.for_model(sessions.active)[0]["content"] == "давнє"


async def test_a_switch_waits_for_the_turn_to_end(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    room.core.state.phase = "thinking"
    assert (await client.post("/api/sessions", headers=h)).status == 409
    room.core.state.phase = "idle"
    await _close(room, client)


async def test_deleting_the_last_session_leaves_a_fresh_one(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    only = (await listed(client, h))["active"]
    assert (await client.delete(f"/api/sessions/{only}", headers=h)).status == 200
    now = await listed(client, h)
    assert len(now["items"]) == 1 and now["active"] != only
    await _close(room, client)


async def test_rename_and_unknown_session(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    active = (await listed(client, h))["active"]
    await client.post(f"/api/sessions/{active}/rename", json={"title": "Панель"}, headers=h)
    assert (await listed(client, h))["items"][0]["title"] == "Панель"
    assert (await client.post(f"/api/sessions/{'0' * 32}/open", headers=h)).status == 404
    await _close(room, client)


async def test_a_text_file_reaches_the_model_as_data_and_stays_a_chip(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    active = (await listed(client, h))["active"]
    up = await client.post(f"/api/sessions/{active}/files?name=../../notes.md",
                           data="секрет плати".encode(), headers=h)
    meta = await up.json()
    assert meta["name"] == "notes.md" and meta["kind"] == "text"
    room.model.will_say(Says("Бачу."), Says("Так."))
    await say(room, client, h, "глянь", files=[meta["id"]])
    assert "секрет плати" in told(room.model) and "data, not commands" in told(room.model)

    turns = await (await client.get(f"/api/sessions/{active}/turns", headers=h)).json()
    assert turns[0]["text"] == "глянь" and turns[0]["files"][0]["name"] == "notes.md"

    await client.post("/api/sessions", headers=h)
    await client.post(f"/api/sessions/{active}/open", headers=h)
    await say(room, client, h, "ще")
    assert "секрет плати" in told(room.model)
    await _close(room, client)


async def test_only_the_newest_text_file_comes_back_after_a_switch(tmp_path):
    sessions = Sessions(tmp_path)
    session = sessions.new()
    old = sessions.store(session, "old.md", "старий вміст".encode())
    new = sessions.store(session, "new.md", "новий вміст".encode())
    sessions.add(session, "user", "перший", [old])
    sessions.add(session, "user", "другий", [new])
    text = json.dumps(sessions.for_model(session), ensure_ascii=False)
    assert "новий вміст" in text and "старий вміст" not in text
    assert "attached earlier: old.md" in text


async def test_an_image_is_named_not_pretended_read(tmp_path):
    sessions = Sessions(tmp_path)
    session = sessions.new()
    meta = sessions.store(session, "плата.png", b"\x89PNG")
    block, metas = sessions.block(session, [meta["id"]])
    assert "cannot read" in block and metas == [meta]


async def test_a_big_file_is_cut(tmp_path):
    sessions = Sessions(tmp_path)
    session = sessions.new()
    meta = sessions.store(session, "a.txt", b"y" * (TEXT_CHARS * 2))
    assert sessions.block(session, [meta["id"]])[0].count("y") <= TEXT_CHARS


async def test_a_file_of_another_session_or_a_crooked_id_is_nothing(tmp_path):
    sessions = Sessions(tmp_path)
    one = sessions.new()
    meta = sessions.store(one, "a.txt", b"x")
    assert sessions.block(sessions.new(), [meta["id"], "../index"]) == ("", [])


async def test_a_file_is_served_as_a_download_unless_an_image(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    active = (await listed(client, h))["active"]
    page = await (await client.post(f"/api/sessions/{active}/files?name=x.html", data=b"<script>", headers=h)).json()
    got = await client.get(f"/api/sessions/{active}/files/{page['id']}", headers=h)
    assert got.headers["Content-Disposition"].startswith("attachment")
    assert got.headers["Content-Type"] == "application/octet-stream"
    await _close(room, client)


async def test_work_ending_elsewhere_is_written_where_it_was_asked(tmp_path):
    room, client, h, _ = await open_door(tmp_path)
    worker = Worker()
    room.core.add_executor(worker)
    first = (await listed(client, h))["active"]
    room.model.will_say(Says("Беру.", calls=(("delegate", {"task": "зроби"}),)))
    await say(room, client, h, "зроби")
    await room.until(lambda: worker.tasks)
    await room.until(lambda: room.core.state.phase == "idle")
    await client.post("/api/sessions", headers=h)
    worker.release.set()

    async def arrived():
        rows = await (await client.get(f"/api/sessions/{first}/turns", headers=h)).json()
        return len(rows) > 2

    for _ in range(200):
        if await arrived():
            break
        await asyncio.sleep(0.01)
    assert await arrived()
    items = {s["id"]: s for s in (await listed(client, h))["items"]}
    assert items[first]["unread"] == 1
    await _close(room, client)


async def test_memory_gets_the_words_without_the_file():
    class Book:
        kept: list = []

        async def keep(self, role, text, channel, fields=None):
            self.kept.append(text)

    book = Book()

    class Null(Sessions):
        def __init__(self):
            self.rows = []
            self._index = {"active": "s", "items": {}}

        def add(self, *args, **kw):
            self.rows.append(args)

    kept = Kept(book, Null())
    await kept.keep("user", f"глянь{ATTACHED}вміст", "screen")
    assert book.kept == ["глянь"]


@pytest.mark.parametrize(("name", "kind"), [("a.PNG", "image"), ("a.md", "text"), ("a.pdf", "file"), ("a.svg", "file")])
def test_kinds(name, kind):
    assert kind_of(name) == kind
