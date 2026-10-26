"""The bench: a real brain, real native hands and virtual boards, over real HTTP.

No network beyond 127.0.0.1 and no live heare: the brain is built on a
temporary home, the models are scripts. What runs is the parts' own code —
the knock, the claim, the acts doors, the control event, the board's report —
so a change to any of them is seen here before it is seen on the desk.

    make bench
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from aiohttp.test_utils import TestServer

from heare import native_hands, sim
from heare.api.server import Screen, build
from heare.config import Settings
from heare.reach.remote import RemoteExecutor
from tests.heare.fakes import Says, Script, open_room

HOST = "bench"
TRUSTED = ["native-hands@bench", "oled-desk@sim", "oled-kitchen@sim"]


class Bench:
    def __init__(self, room, server, settings, boards, hand_model, tasks):
        self.room, self.server, self.settings = room, server, settings
        self.boards, self.hand_model, self.tasks = boards, hand_model, tasks

    async def until(self, check, seconds: float = 5.0, what: str = "") -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while not check():
            if loop.time() > end:
                raise AssertionError(f"the bench waited in vain: {what}")
            await asyncio.sleep(0.02)

    def ask(self, voice: Says, *hand: Says | str) -> None:
        """What the voice does with the next words, and what the hands' model says."""
        self.room.model.will_say(voice)
        self.hand_model.will_say(*hand)


@contextlib.asynccontextmanager
async def bench(tmp_path, names=("desk", "kitchen")):
    (tmp_path / "parts.json").write_text(json.dumps(TRUSTED), encoding="utf-8")
    room = await open_room()
    room.core.add_output(Screen(room.core))
    brain = Settings(
        path=tmp_path / "config.toml", api_token_file=tmp_path / "token",
        screens_dir=tmp_path / "screens", parts_file=tmp_path / "parts.json",
        parts_dir=tmp_path / "parts", part_tokens_file=tmp_path / "part_tokens.json",
        pair_codes_file=tmp_path / "pair_codes.json", memory_dir=tmp_path / "memory",
        heard_dir=tmp_path / "heard", backups_dir=tmp_path / "home" / "backups",
    )
    app = build(room.core, brain,
                make_executor=lambda name, about: RemoteExecutor(name, about, claim_wait_s=0.5))
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    parts = Settings(path=tmp_path / "part.toml", api_token_file=tmp_path / "token",
                     machine_token_file=tmp_path / "no-machine-token",
                     url=f"http://127.0.0.1:{server.port}")
    boards = {name: sim.Board(name) for name in names}
    hand_model = Script()
    tasks = [asyncio.create_task(sim.live(b, parts)) for b in boards.values()]
    tasks.append(asyncio.create_task(native_hands.run(parts, hand_model, HOST)))
    found = Bench(room, server, parts, boards, hand_model, tasks)
    try:
        await found.until(lambda: "native-hands" in room.core.executors
                          and all(b.let_in.is_set() for b in boards.values()),
                          what="the hands and the boards let in")
        yield found
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.close()
        await room.close()


def _done(bench: Bench) -> list:
    return [t for t in bench.room.core.state.tasks.values() if t.status != "running"]


async def test_words_go_from_the_voice_through_the_hands_to_the_board(tmp_path):
    async with bench(tmp_path, names=("desk",)) as b:
        b.ask(Says(calls=(("delegate", {"task": "покажи hello на платі"}),)),
              Says(calls=(("show", {"text": "hello", "seconds": 5}),)), "Показав.")

        b.room.core.hear(native_hands_message("покажи hello на платі"))
        await b.until(lambda: b.boards["desk"].shown == "hello", what="hello on the board")
        await b.until(lambda: _done(b), what="the task finished")

        task = _done(b)[0]
        assert (task.executor, task.status, task.result) == ("native-hands", "done", "Показав.")
        assert b.boards["desk"].done == [("show", {"text": "hello", "seconds": 5})]


async def test_a_pin_is_switched_only_on_a_yes(tmp_path):
    async with bench(tmp_path, names=("desk",)) as b:
        b.ask(Says(calls=(("delegate", {"task": "увімкни pin3"}),)),
              Says(calls=(("switch", {"pin": "pin3", "on": True}),)), "Увімкнув.")

        b.room.core.hear(native_hands_message("увімкни pin3"))
        await b.until(lambda: b.room.core.state.permission is not None, what="the question")
        assert b.boards["desk"].pins == {}
        b.room.core.permit(b.room.core.state.permission.id, True)

        await b.until(lambda: b.boards["desk"].pins.get("pin3") == 1, what="pin3 on")


async def test_the_board_reports_what_it_was_told(tmp_path):
    async with bench(tmp_path, names=("desk",)) as b:
        b.ask(Says(calls=(("delegate", {"task": "блимни"}),)),
              Says(calls=(("light", {"mode": "blink"}),)), "Блимаю.")

        b.room.core.hear(native_hands_message("блимни"))
        await b.until(lambda: b.boards["desk"].led == "blink", what="the light blinking")


async def test_two_boards_are_both_in_the_brain(tmp_path):
    # Б3: the brain knows both; the hands still act through the first only.
    async with bench(tmp_path) as b:
        import httpx

        token = (tmp_path / "token").read_text()
        async with httpx.AsyncClient() as client:
            acting = (await client.get(f"{b.settings.base}/api/acts",
                                       headers={"X-Heare-Token": token})).json()
        assert set(acting) == {"oled-desk@sim", "oled-kitchen@sim"}


async def test_a_deaf_board_leaves_the_hands_saying_it_was_done(tmp_path):
    # Б2 made visible: an act has no answer, so a board that dropped it is
    # reported as done. Skills with a reply (І4) turn this test around.
    async with bench(tmp_path, names=("desk",)) as b:
        b.boards["desk"].deaf = True
        b.ask(Says(calls=(("delegate", {"task": "блимни"}),)),
              Says(calls=(("light", {"mode": "blink"}),)), "Блимаю.")

        b.room.core.hear(native_hands_message("блимни"))
        await b.until(lambda: _done(b), what="the task finished")

        assert _done(b)[0].status == "done"
        assert b.boards["desk"].led == "off"


def native_hands_message(text: str):
    from heare.core.forms import Message

    return Message(text=text, channel="screen")


@pytest.fixture(autouse=True)
def _quiet_parts(monkeypatch):
    """A part waits a second between tries; the bench has no second to give."""
    from heare import part

    monkeypatch.setattr(part, "RETRY_S", 0.05, raising=False)
