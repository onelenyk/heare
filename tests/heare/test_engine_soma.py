"""The soma: wants that grow, hold, and let go."""

from __future__ import annotations

from heare.engine.soma import Soma


def soma() -> Soma:
    return Soma()


def run(w: Soma, seconds: float, step: float = 1.0) -> None:
    left = seconds
    while left > 0:
        piece = min(step, left)
        w.tick(piece)
        left -= piece


def test_time_alone_makes_a_want_grow():
    w = soma()
    w.feed("task_opened", thing_id="7", kind="task", label="сайт")
    before = w.wants[0].care
    run(w, 3600)
    assert w.wants[0].care > before


def test_a_failure_presses_at_once():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    hot = w.hottest()
    assert hot is not None and hot.pressing >= 0.5
    assert hot.verb == "finish"


def test_an_opened_task_presses_only_when_it_has_grown():
    w = soma()
    w.feed("task_opened", thing_id="7", kind="task", label="сайт")
    assert w.hottest().pressing < 0.5
    run(w, 3 * 3600)
    assert w.hottest().pressing >= 0.5


def test_two_tasks_bind_to_their_own_things():
    w = soma()
    w.feed("task_opened", thing_id="a", kind="task", label="a")
    w.feed("task_opened", thing_id="b", kind="task", label="b")
    finishes = [x for x in w.wants if x.verb == "finish"]
    assert len(finishes) == 2
    assert {x.thing_id for x in finishes} == {"a", "b"}


def test_done_closes_one_thing_and_leaves_a_trace():
    w = soma()
    w.feed("task_opened", thing_id="a", kind="task", label="a")
    w.feed("task_opened", thing_id="b", kind="task", label="b")
    run(w, 3 * 3600)
    w.feed("task_done", thing_id="a", kind="task", label="a")
    assert "a" in w.trace
    assert any(t.id == "a" and t.state == "finished" for t in w.things)
    b = next(x for x in w.wants if x.thing_id == "b")
    assert b.care > 0.0


def test_without_events_nothing_is_bound():
    w = soma()
    run(w, 7200)
    assert w.wants == []
    assert w.things == []


def test_things_are_never_deleted():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.feed("task_done", thing_id="7", kind="task", label="сайт")
    assert any(t.id == "7" and t.state == "finished" for t in w.things)


def test_engage_hands_the_burden_over():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    before = w.wants[0].care
    w.engage("7")
    assert w.wants[0].carried is True
    assert w.wants[0].pressing == 0.0
    assert w.wants[0].care < before


def test_ignored_leaves_her_alone_with_it():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    assert w.wants[0].said == 1
    w.ignored()
    assert w.wants[0].said == 0
    assert w.wants[0].carried is False
    assert w.wants[0].pressing > 0.0


def test_spoke_resets_pressing_and_costs_energy():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    assert w.wants[0].pressing == 0.0
    assert w.wants[0].said == 1
    assert w.energy < 1.0


def test_a_reused_id_gets_its_own_label():
    w = soma()
    w.feed("task_opened", thing_id="1", kind="task", label="old")
    w.feed("task_done", thing_id="1", kind="task", label="old")
    w.feed("task_opened", thing_id="1", kind="task", label="new")
    thing = w.thing("1")
    assert thing is not None
    assert (thing.label, thing.state) == ("new", "alive")


def test_a_carried_want_does_not_re_press_on_time():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    w.engage("7")
    run(w, 3 * 3600)
    assert w.hottest().pressing < 0.5


def test_the_state_is_english_without_numbers():
    w = soma()
    w.feed("task_failed", thing_id="heare", kind="task", label="міграція heare")
    run(w, 7200)
    text = w.state()
    assert "міграція heare" in text
    assert not any(char.isdigit() for char in text)
    for word in ("drive", "need", "level", "urgency", "closure", "attunement"):
        assert word not in text.lower()


def test_what_she_has_said_fades_instead_of_growing():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    heard = w.wants[0].care
    run(w, 6 * 24 * 3600)
    assert w.wants[0].care < heard / 3, "a spoken want keeps its care forever"


def test_a_spoken_want_is_forgotten_in_a_fortnight():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    run(w, 14 * 24 * 3600)
    assert w.wants == [], "it was said, nobody answered, and it never faded"


def test_what_the_person_took_over_fades_too():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.engage("7")
    run(w, 14 * 24 * 3600)
    assert w.wants == [], "a carried want never leaves"


def test_an_unheard_want_still_grows():
    w = soma()
    w.feed("task_opened", thing_id="7", kind="task", label="сайт")
    before = w.wants[0].care
    run(w, 24 * 3600)
    assert w.wants[0].care > before, "an unspoken want must keep rising"


def test_the_mood_lets_go_of_what_she_said():
    w = soma()
    for n in range(1, 11):
        w.feed("task_failed", thing_id=str(n), kind="task", label=f"справа {n}")
    assert "heavy" in w.state(), "ten unheard failures weigh on her"
    for n in range(1, 11):
        w.spoke(str(n))
    assert "heavy" not in w.state(), "she broods over what she has already said"
    run(w, 7 * 24 * 3600)
    assert "heavy" not in w.state()


def test_the_list_of_wants_is_bounded():
    from heare.engine.soma import HELD_AT_MOST

    w = soma()
    for n in range(HELD_AT_MOST + 8):
        w.feed("task_failed", thing_id=str(n), kind="task", label=f"справа {n}")
        run(w, 5 * 3600, step=600)
    assert len(w.wants) == HELD_AT_MOST
    oldest = w.thing("0")
    assert oldest is not None and oldest.state == "released"
    newest = w.thing(str(HELD_AT_MOST + 7))
    assert newest is not None and newest.state == "alive"


def test_age_is_discounted_in_the_ranking():
    w = soma()
    w.feed("task_failed", thing_id="old", kind="task", label="старе")
    run(w, 6 * 24 * 3600, step=3600)
    w.feed("task_failed", thing_id="new", kind="task", label="нове")
    assert w.hottest() is not None
    assert w.hottest().thing_id == "new", "the oldest thing presses hardest forever"


def test_letting_one_go_by_hand_costs_no_energy():
    w = soma()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    before = w.energy
    w.spoke("7", spent=False)
    assert w.energy == before
    assert w.wants[0].said == 1
