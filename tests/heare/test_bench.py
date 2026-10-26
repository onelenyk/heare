"""The bench: a real brain, real native hands and virtual boards, over real HTTP.

No network beyond 127.0.0.1 and no live heare: the brain is built on a
temporary home, the models are scripts. What runs is the parts' own code —
the knock with skills, the claim, the call and its answer, the board's
report — so a change to any of them is seen here before it is seen on the
desk.

    make bench
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json

import pytest
from aiohttp.test_utils import TestServer

from heare import native_hands, sim
from heare.api import skills
from heare.api.server import Screen, build
from heare.config import Settings
from heare.core.forms import Message, Task
from heare.model import route
from heare.model.route import Router
from heare.reach.remote import RemoteExecutor
from tests.heare.fakes import Says, Script, Worker, open_room

HOST = "bench"


class Bench:
    def __init__(self, room, settings, boards, hand_model):
        self.room, self.settings = room, settings
        self.boards, self.hand_model = boards, hand_model

    async def until(self, check, seconds: float = 5.0, what: str = "") -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while not check():
            if loop.time() > end:
                raise AssertionError(f"the bench waited in vain: {what}")
            await asyncio.sleep(0.02)

    def say(self, words: str, *hand: Says | str) -> None:
        """The person's words, the voice handing them over, and what the
        hands' model does with them."""
        self.room.model.will_say(Says(calls=(("delegate", {"task": words}),)))
        self.hand_model.will_say(*hand)
        self.room.core.hear(Message(text=words, channel="screen"))

    def done(self) -> list:
        return [t for t in self.room.core.state.tasks.values() if t.status != "running"]

    async def finished(self):
        await self.until(lambda: self.done(), what="the task finished")
        return self.done()[0]


@contextlib.asynccontextmanager
async def bench(tmp_path, *boards: sim.Board):
    trusted = ["native-hands@bench", *(b.pid for b in boards)]
    (tmp_path / "parts.json").write_text(json.dumps(trusted), encoding="utf-8")
    held: dict = {}
    # The brain's own rule for who does what, as it runs live.
    room = await open_room(delegator=Router(None, lambda: held["room"].core.state.tasks))
    held["room"] = room
    room.core.add_output(Screen(room.core))
    brain = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts", part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json", memory_dir=tmp_path / "memory",
        heard_dir=tmp_path / "heard", backups_dir=tmp_path / "home" / "backups",
    )
    app = build(room.core, brain, make_executor=lambda name, about: route.executor(
        name, about, lambda n, a: RemoteExecutor(n, a, claim_wait_s=0.5),
        lambda: list(room.core.executors.values()), lambda: room.core.state.tasks))
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    parts = Settings(path=tmp_path / "part.toml", api_token_file=tmp_path / "token",
                     machine_token_file=tmp_path / "no-machine-token",
                     url=f"http://127.0.0.1:{server.port}")
    hand_model = Script()
    tasks = [asyncio.create_task(sim.live(b, parts)) for b in boards]
    tasks.append(asyncio.create_task(native_hands.run(parts, hand_model, HOST)))
    found = Bench(room, parts, {b.pid: b for b in boards}, hand_model)
    try:
        await found.until(lambda: "native-hands" in room.core.executors
                          and all(b.let_in.is_set() for b in boards),
                          what="the hands and the boards let in")
        yield found
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.close()
        await room.close()


async def test_words_go_from_the_voice_through_the_hands_to_the_board(tmp_path):
    desk = sim.Board("desk")
    async with bench(tmp_path, desk) as b:
        b.say("покажи hello на платі",
              Says(calls=(("desk__show", {"text": "hello", "seconds": 5}),)), "Показав.")

        task = await b.finished()

        assert desk.shown == "hello"
        assert (task.executor, task.status, task.result) == ("native-hands", "done", "Показав.")
        # The board's own answer is what the hands' model read.
        assert b.hand_model.asked[1][-1]["content"] == "показано: hello"


async def test_an_svg_the_model_writes_reaches_the_board_as_its_pixels(tmp_path):
    desk = sim.Board("desk")
    heart = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 40">'
             '<circle cx="36" cy="20" r="14" fill="white"/></svg>')
    async with bench(tmp_path, desk) as b:
        b.say("намалюй коло", Says(calls=(("desk__image", {"svg": heart}),)), "Намалював.")

        await b.finished()

        assert len(desk.picture) == 360
        assert desk.picture[20 * 9 + 4] & 0x08          # (36, 20): lit
        assert not desk.picture[0]                       # the corner: dark
        assert b.hand_model.asked[1][-1]["content"] == "картинку показано"


async def test_a_scene_reaches_the_board_with_its_icons_drawn_and_updates_after(tmp_path):
    desk = sim.Board("desk")
    sun = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
           '<circle cx="8" cy="8" r="5" fill="white"/></svg>')
    weather = {"id": "weather", "fields": {"t": 21},
               "layout": {"cols": [{"widget": "icon", "svg": sun},
                                   {"w": 2, "widget": "number", "bind": "t", "unit": "°"}]}}
    async with bench(tmp_path, desk) as b:
        b.say("покажи погоду", Says(calls=(("desk__screen", {"scene": weather, "layer": "pin"}),)),
              "Показав.")
        await b.finished()
        b.say("тепер 22", Says(calls=(("desk__update", {"id": "weather", "fields": {"t": 22}}),)),
              "Оновив.")
        await b.until(lambda: desk.scenes.get("weather", {}).get("fields", {}).get("t") == 22,
                      what="the field updated")

        icon = desk.scenes["weather"]["layout"]["cols"][0]
        assert "svg" not in icon and len(base64.b64decode(icon["bits"])) == 32


async def test_a_world_changing_skill_waits_for_a_yes(tmp_path):
    desk = sim.Board("desk")
    async with bench(tmp_path, desk) as b:
        b.say("увімкни pin3", Says(calls=(("desk__pin3", {"on": True}),)), "Увімкнув.")

        await b.until(lambda: b.room.core.state.permission is not None, what="the question")
        assert desk.pins == {}
        b.room.core.permit(b.room.core.state.permission.id, True)

        await b.until(lambda: desk.pins.get("pin3") == 1, what="pin3 on")


async def test_a_sensor_is_only_a_skill_its_board_brings(tmp_path):
    kitchen = sim.Board("kitchen", about="на кухні", temperature=19.0)
    kitchen.skills.append(dict(sim.SENSOR))
    async with bench(tmp_path, kitchen) as b:
        b.say("яка температура на кухні",
              Says(calls=(("kitchen__temperature", {}),)), "19 градусів.")

        task = await b.finished()

        assert task.result == "19 градусів."
        assert b.hand_model.asked[1][-1]["content"] == "19.0"
        assert "kitchen__temperature" in b.hand_model.offered[0]


async def test_two_boards_are_told_apart_by_their_names(tmp_path):
    desk, kitchen = sim.Board("desk"), sim.Board("kitchen", about="на кухні")
    async with bench(tmp_path, desk, kitchen) as b:
        b.say("покажи hi на кухні",
              Says(calls=(("kitchen__show", {"text": "hi"}),)), "Показав.")

        await b.finished()

        assert kitchen.shown == "hi" and desk.shown == ""
        assert {"desk__show", "kitchen__show"} <= set(b.hand_model.offered[0])


async def test_twins_of_one_name_are_asked_about(tmp_path):
    one, two = sim.Board("desk", host="one"), sim.Board("desk", host="two")
    async with bench(tmp_path, one, two) as b:
        b.say("покажи hi", Says(calls=(("desk__show", {"text": "hi"}),)), "Показав.")

        await b.until(lambda: b.room.core.state.question is not None, what="which board")
        b.room.core.hear(Message(text="2", channel="screen"))

        await b.until(lambda: two.shown == "hi", what="the second twin shows it")
        assert one.shown == ""


async def test_a_board_that_does_not_answer_is_not_said_to_have_done_it(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "CALL_S", 0.3)
    desk = sim.Board("desk", deaf=True)
    async with bench(tmp_path, desk) as b:
        b.say("блимни", Says(calls=(("desk__light", {"mode": "blink"}),)), "Не вийшло.")

        await b.finished()

        assert b.hand_model.asked[1][-1]["content"] == "Не вийшло: частина не відповіла"
        assert desk.led == "off"


async def test_what_is_not_for_the_bodies_goes_on_to_the_cli_hands(tmp_path):
    async with bench(tmp_path, sim.Board("desk")) as b:
        cli = Worker("opencode", result="12 файлів.")
        cli.release.set()
        b.room.core.add_executor(cli)
        b.say("скільки файлів у завантаженнях", Says(calls=(
            ("pass_to_cli_hands", {"task": "Порахуй файли в ~/Downloads"}),)))

        task = await b.finished()

        assert cli.tasks == ["Порахуй файли в ~/Downloads"]
        assert (task.executor, task.status, task.result) == ("opencode", "done", "12 файлів.")


async def test_work_that_goes_on_stays_with_its_hands(tmp_path):
    async with bench(tmp_path, sim.Board("desk")) as b:
        cli = Worker("opencode", result="Готово.")
        cli.release.set()
        b.room.core.add_executor(cli)
        b.room.core.state.tasks[99] = Task(99, "напиши скрипт", "opencode", status="done")
        b.room.model.will_say(Says(calls=(("delegate", {"task": "а тепер запусти",
                                                        "continues": True}),)))
        b.room.core.hear(Message(text="а тепер запусти", channel="screen"))

        await b.until(lambda: cli.tasks, what="the cli hands took it")
        assert cli.tasks[0].startswith("а тепер запусти")
        assert b.hand_model.asked == []


@pytest.fixture(autouse=True)
def _quiet_parts(monkeypatch):
    """A part waits a second between tries; the bench has no second to give."""
    from heare import part

    monkeypatch.setattr(part, "RETRY_S", 0.05, raising=False)
