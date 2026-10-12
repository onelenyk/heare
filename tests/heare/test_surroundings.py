"""What is going on around it: known cheaply, and acted on by rule."""

from __future__ import annotations

import asyncio

from heare.core import Message
from heare.surroundings import sound
from heare.surroundings.watch import Surroundings
from tests.heare.fakes import Says, Worker, open_room


def test_the_sense_answers_with_names_and_never_breaks():
    heard = sound.playing()
    assert isinstance(heard, list) and all(isinstance(n, str) for n in heard)


async def test_while_something_plays_only_the_name_gets_an_answer():
    room = await open_room()
    room.line.port.note("playing", ["Music"])
    room.core.hear(Message(text="Я знову закохався, і це назавжди", channel="text",
                           addressed=False))
    await room.settled()
    assert room.model.asked == [] and room.page.said == []  # not even asked
    assert [e.data["why"] for e in room.core.state.journal if e.kind == "overheard"] == [
        "playing"
    ]
    room.model.will_say("Так, чую.")
    room.says("Дока, ти мене чуєш?")
    await room.settled()
    assert room.page.said == ["Так, чую. "]
    await room.close()


async def test_when_it_is_quiet_again_he_is_understood_without_the_name():
    room = await open_room()
    room.line.port.note("playing", ["Music"])
    room.line.port.note("playing", [])
    room.model.will_say("Так.", "Так, тепер тихо.")
    room.core.hear(Message(text="О, нарешті тихо, правда?", channel="text",
                           addressed=False))
    await room.settled()
    assert room.page.said == ["Так, тепер тихо. "]
    await room.close()


async def test_it_knows_its_work_and_the_room_without_being_told():
    room = await open_room()
    worker = Worker()
    room.core.add_executor(worker)
    room.core.delegate("створи десять файлів")
    await room.until(lambda: worker.tasks)
    room.says("стоп")
    await room.settled()
    room.line.port.note("playing", ["Google Chrome"])
    room.model.will_say("Нічого, усе зупинено.")
    room.says("Що ти зараз робиш?")
    await room.settled()
    prompt = room.model.asked[-1][0]["content"]
    assert "зупинено (worker): створи десять файлів" in prompt
    assert "грає звук (Google Chrome)" in prompt
    assert room.core.state.snapshot()["around"] == {"playing": ["Google Chrome"]}
    await room.close()


async def test_a_gap_between_two_tracks_is_not_silence():
    heard = iter([["Music"], [], ["Music"], [], [], [], [], []])
    told: list = []

    class Port:
        def note(self, key, value):
            told.append((key, list(value)))

    watch = Surroundings(sense=lambda: next(heard, []))
    import heare.surroundings.watch as module

    module.EVERY_S, was = 0.01, module.EVERY_S
    module.QUIET_AFTER_S, quiet = 0.025, module.QUIET_AFTER_S
    try:
        run = asyncio.create_task(watch.run(Port()))
        await asyncio.sleep(0.15)
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
    finally:
        module.EVERY_S, module.QUIET_AFTER_S = was, quiet
    assert told == [("playing", ["Music"]), ("playing", [])]


async def test_a_drawing_request_is_not_swallowed_by_the_rule():
    """Typed and clicked messages are addressed by nature: the rule is
    about speech that names nobody."""
    room = await open_room()
    room.line.port.note("playing", ["Music"])
    room.model.will_say(Says("Ось."))
    room.says("Покажи список")
    await room.settled()
    assert room.page.said == ["Ось. "]
    await room.close()
