"""The voice's verbs by category, and the one door to the world.

The voice keeps the verbs about the conversation (recall, remind, forget)
and sends everything about the world — the screen, the canvas, the
machine — through `delegate`. Unit tests prove the registry; these prove
the path: the scripted model calls the door, the real toolbox starts the
real worker, the worker is shown the screen and the conversation, and
its answer comes back and is said.

Only the worker's model call is replaced (`Hands._chat`), the same way
the room replaces the voice's: a test that cannot say what the model
answers is not testing anything downstream of it.
"""

from __future__ import annotations

import pytest

from tests.e2e.room import Says, close_room, hire, lines, open_room, until

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.e2e_text]


@pytest.fixture
async def room(tmp_path):
    r = await open_room(tmp_path)
    try:
        yield r
    finally:
        await close_room(r)


@pytest.fixture
def worker(monkeypatch):
    return lambda *answers: hire(monkeypatch, *answers)


# ── what the voice is offered ─────────────────────────────────────────


async def test_the_voice_sees_three_verbs_and_one_door(room) -> None:
    """Assembled by the composition root, not by a test: a verb wired
    nowhere, or offered with nothing behind it, fails here."""
    names = {s["function"]["name"] for s in room.loop.toolbox.schemas}
    assert names == {"delegate", "recall", "remind"}


async def test_forget_appears_only_when_the_room_is_kept(tmp_path) -> None:
    kept = await open_room(tmp_path, features={"hear_all": True})
    try:
        names = {s["function"]["name"] for s in kept.loop.toolbox.schemas}
        assert "forget" in names
    finally:
        await close_room(kept)


# ── the world goes through the door ──────────────────────────────────


async def test_the_screen_is_asked_of_the_hands_and_the_answer_is_said(
    room, worker
) -> None:
    """«Що в мене на екрані?» used to be the voice's own verb. Now it is a
    question about the world: the door is called, the worker is offered
    the eye, and what it saw comes back in the voice."""
    hands = worker("На екрані відкритий браузер, у ньому пошта.")
    room.will_say(
        Says(calls=(("delegate", {"task": "подивись, що на екрані"}),)),
        "У тебе відкрита пошта в браузері.",
    )

    ack = await room.told("Дока, що в мене на екрані?")

    assert ack == "Прийнято, роблю."
    await until(lambda: hands.calls)
    assert {"look_at_screen", "read_display", "clear_display"} <= hands.offered()
    await until(lambda: "У тебе відкрита пошта в браузері." in lines(room))


async def test_the_hands_hear_what_was_just_said(room, worker) -> None:
    """«А другий процес?» means nothing on its own. The worker is given
    the last turns, so the follow-up lands on the question before it."""
    hands = worker(
        "Найбільше навантажує Chrome, далі Telegram.",
        "Другий — Telegram, близько двадцяти відсотків.",
    )
    room.will_say(
        Says(calls=(("delegate", {"task": "що найбільше навантажує комп'ютер"}),)),
        "Найбільше Chrome, потім Telegram.",
        Says(calls=(("delegate", {"task": "скільки навантажує другий процес"}),)),
        "Telegram, десь двадцять відсотків.",
    )

    await room.told("Дока, глянь, що жере ресурси")
    await until(lambda: "Найбільше Chrome, потім Telegram." in lines(room))
    await room.told("а другий процес?")
    await until(lambda: len(hands.calls) == 2)

    seen = hands.context()
    assert "глянь, що жере ресурси" in seen
    assert "Chrome" in seen


async def test_clearing_the_canvas_is_work_for_the_hands(room, worker) -> None:
    hands = worker("Очистила.")
    room.will_say(
        Says(calls=(("delegate", {"task": "очисти канвас"}),)),
        "Готово, канвас чистий.",
    )

    await room.told("Дока, очисти канвас")

    await until(lambda: hands.calls)
    assert hands.calls[0][0][-1]["content"] == "очисти канвас"
    await until(lambda: "Готово, канвас чистий." in lines(room))
