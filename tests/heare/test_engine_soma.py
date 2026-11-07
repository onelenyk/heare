"""The body: needs that grow, hold, and let go."""

from __future__ import annotations

from heare.engine.soma import ACTIVATION, Body

def body() -> Body:
    return Body()


def run(w: Body, seconds: float, step: float = 1.0) -> None:
    left = seconds
    while left > 0:
        piece = min(step, left)
        w.tick(piece)
        left -= piece


def test_time_alone_makes_a_need_grow():
    w = body()
    w.feed("silence_long")
    before = w.needs[0].level
    run(w, 3600)
    assert w.needs[0].level > before


def test_a_task_becomes_an_intention_only_when_it_grows():
    w = body()
    w.feed("task_opened", "heare", "міграція heare")
    assert w.propose() is None
    run(w, 3600)
    assert w.propose() is not None
    assert w.propose().drive == "closure"


def test_two_tasks_bind_to_their_own_things():
    w = body()
    w.feed("task_opened", "a", "a")
    w.feed("task_opened", "b", "b")
    closure = [n for n in w.needs if n.drive == "closure"]
    assert len(closure) == 2
    assert {n.about for n in closure} == {"a", "b"}


def test_closing_one_task_does_not_close_the_other():
    w = body()
    w.feed("task_opened", "a", "a")
    w.feed("task_opened", "b", "b")
    run(w, 3600)
    first = next(n for n in w.needs if n.about == "a")
    w.feed("task_done", "a", "a")
    assert first is not None
    other = next(n for n in w.needs if n.about == "b")
    assert other.level >= ACTIVATION


def test_without_events_nothing_is_bound_to_a_thing():
    w = body()
    run(w, 7200)
    assert [n for n in w.needs if n.about != "self"] == []


def test_nothing_appears_from_nowhere():
    w = body()
    run(w, 7200)
    assert {n.drive for n in w.needs} <= {"connection"}


def test_the_dominant_need_holds_for_a_while():
    w = body()
    w.feed("task_opened", "a", "a")
    run(w, 60)
    first = w.dominant
    w.feed("interrupted", "owner", "owner")
    w.feed("interrupted", "owner", "owner")
    run(w, 100)
    assert w.dominant == first
    run(w, 400)
    assert w.dominant != first


def test_an_interruption_takes_the_floor_and_gives_it_back():
    w = body()
    w.feed("task_opened", "a", "a")
    run(w, 3600)
    first = w.active
    first_dom = w.dominant
    assert first is not None
    w.feed("interrupted", "owner", "owner")
    w.feed("interrupted", "owner", "owner")
    run(w, 600)
    assert w.dominant != first_dom
    attunement = next(n for n in w.needs if n.drive == "attunement")
    w.perform(attunement.id)
    run(w, 600)
    assert w.active == first


def test_commitment_grows_while_held():
    w = body()
    w.feed("task_opened", "a", "a")
    run(w, 3600)
    early = w.propose()
    assert early is not None
    held_id = early.id
    early_commitment = early.commitment
    run(w, 6000)
    same = next(i for i in w.intentions if i.id == held_id)
    assert same.commitment > early_commitment


def test_a_short_failure_does_not_kill_the_intention():
    w = body()
    w.feed("task_opened", "a", "a")
    run(w, 3600)
    held = w.propose()
    assert held is not None
    w.feed("task_failed", "a", "a")
    run(w, 60)
    again = next(i for i in w.intentions if i.id == held.id)
    assert again.status in ("held", "active")


def test_an_expired_intention_is_dropped():
    w = body()
    w.feed("task_opened", "a", "a")
    run(w, 3600)
    held = w.propose()
    assert held is not None
    run(w, 100_000)
    assert held.status in ("dropped", "done")


def test_intentions_do_not_multiply():
    w = body()
    for index in range(30):
        w.feed("task_opened", f"t{index}", f"t{index}")
        run(w, 60)
    open_ones = [i for i in w.intentions if i.status in ("held", "active")]
    assert len(open_ones) <= 12


def test_the_state_is_written_for_a_model_not_for_a_reader_of_numbers():
    w = body()
    w.feed("task_opened", "heare", "міграція heare")
    run(w, 7200)
    text = w.state()
    assert "міграція heare" in text
    assert not any(char.isdigit() for char in text)
    for word in ("drive", "need", "intent", "level", "urgency", "closure"):
        assert word not in text.lower()


def test_performing_eases_more_the_first_time_than_the_tenth():
    w = body()
    w.feed("task_opened", "a", "a")
    run(w, 3600)
    need = next(n for n in w.needs if n.drive == "closure")
    gains: list[float] = []
    for _ in range(10):
        before = need.level
        w.perform(need.id)
        gains.append(before - need.level)
    assert gains[0] > gains[9]
