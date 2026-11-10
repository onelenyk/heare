"""The attention: wants that grow, hold, and let go."""

from __future__ import annotations

from heare.engine.attention import Attention


def attention() -> Attention:
    return Attention()


def run(w: Attention, seconds: float, step: float = 1.0) -> None:
    left = seconds
    while left > 0:
        piece = min(step, left)
        w.tick(piece)
        left -= piece


def test_time_alone_makes_a_want_grow():
    w = attention()
    w.feed("task_opened", thing_id="7", kind="task", label="сайт")
    before = w.wants[0].care
    run(w, 3600)
    assert w.wants[0].care > before


def test_a_failure_presses_at_once():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    hot = w.hottest()
    assert hot is not None and hot.pressing >= 0.5
    assert hot.verb == "finish"


def test_an_opened_task_presses_only_when_it_has_grown():
    w = attention()
    w.feed("task_opened", thing_id="7", kind="task", label="сайт")
    assert w.hottest().pressing < 0.5
    run(w, 3 * 3600)
    assert w.hottest().pressing >= 0.5


def test_two_tasks_bind_to_their_own_things():
    w = attention()
    w.feed("task_opened", thing_id="a", kind="task", label="a")
    w.feed("task_opened", thing_id="b", kind="task", label="b")
    finishes = [x for x in w.wants if x.verb == "finish"]
    assert len(finishes) == 2
    assert {x.thing_id for x in finishes} == {"a", "b"}


def test_done_closes_one_thing_and_leaves_a_trace():
    w = attention()
    w.feed("task_opened", thing_id="a", kind="task", label="a")
    w.feed("task_opened", thing_id="b", kind="task", label="b")
    run(w, 3 * 3600)
    w.feed("task_done", thing_id="a", kind="task", label="a")
    assert "a" in w.trace
    assert any(t.id == "a" and t.state == "finished" for t in w.things)
    b = next(x for x in w.wants if x.thing_id == "b")
    assert b.care > 0.0


def test_without_events_nothing_is_bound():
    w = attention()
    run(w, 7200)
    assert w.wants == []
    assert w.things == []


def test_a_finished_task_is_remembered():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.feed("task_done", thing_id="7", kind="task", label="сайт")
    assert any(t.id == "7" and t.state == "finished" for t in w.things)


def test_engage_hands_the_burden_over():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    before = w.wants[0].care
    w.engage("7")
    assert w.wants[0].carried is True
    assert w.wants[0].pressing == 0.0
    assert w.wants[0].care < before


def test_ignored_leaves_her_alone_with_it():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    assert w.wants[0].said == 1
    w.ignored()
    assert w.wants[0].said == 0
    assert w.wants[0].carried is False
    assert w.wants[0].pressing > 0.0


def test_spoke_resets_pressing_and_costs_energy():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    assert w.wants[0].pressing == 0.0
    assert w.wants[0].said == 1
    assert w.energy < 1.0


def test_a_reused_id_gets_its_own_label():
    w = attention()
    w.feed("task_opened", thing_id="1", kind="task", label="old")
    w.feed("task_done", thing_id="1", kind="task", label="old")
    w.feed("task_opened", thing_id="1", kind="task", label="new")
    thing = w.thing("1")
    assert thing is not None
    assert (thing.label, thing.state) == ("new", "alive")


def test_a_carried_want_does_not_re_press_on_time():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    w.engage("7")
    run(w, 3 * 3600)
    assert w.hottest().pressing < 0.5


def test_the_state_is_english_without_numbers():
    w = attention()
    w.feed("task_failed", thing_id="heare", kind="task", label="міграція heare")
    run(w, 7200)
    text = w.state()
    assert "міграція heare" in text
    assert not any(char.isdigit() for char in text)
    for word in ("drive", "need", "level", "urgency", "closure", "attunement"):
        assert word not in text.lower()


def test_what_she_has_said_fades_instead_of_growing():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    heard = w.wants[0].care
    run(w, 6 * 24 * 3600)
    assert w.wants[0].care < heard / 3, "a spoken want keeps its care forever"


def test_a_spoken_want_is_forgotten_in_a_fortnight():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.spoke("7")
    run(w, 14 * 24 * 3600)
    assert w.wants == [], "it was said, nobody answered, and it never faded"


def test_what_the_person_took_over_fades_too():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    w.engage("7")
    run(w, 14 * 24 * 3600)
    assert w.wants == [], "a carried want never leaves"


def test_an_unheard_want_still_grows():
    w = attention()
    w.feed("task_opened", thing_id="7", kind="task", label="сайт")
    before = w.wants[0].care
    run(w, 24 * 3600)
    assert w.wants[0].care > before, "an unspoken want must keep rising"


def test_the_mood_lets_go_of_what_she_said():
    w = attention()
    for n in range(1, 11):
        w.feed("task_failed", thing_id=str(n), kind="task", label=f"справа {n}")
    assert "heavy" in w.state(), "ten unheard failures weigh on her"
    for n in range(1, 11):
        w.spoke(str(n))
    assert "heavy" not in w.state(), "she broods over what she has already said"
    run(w, 7 * 24 * 3600)
    assert "heavy" not in w.state()


def test_the_list_of_wants_is_bounded():
    from heare.engine.attention import HELD_AT_MOST

    w = attention()
    for n in range(HELD_AT_MOST + 8):
        w.feed("task_failed", thing_id=str(n), kind="task", label=f"справа {n}")
        run(w, 5 * 3600, step=600)
    assert len(w.wants) == HELD_AT_MOST
    assert w.thing("0") is None, "a released thing stayed in the file"
    newest = w.thing(str(HELD_AT_MOST + 7))
    assert newest is not None and newest.state == "alive"


def test_age_is_discounted_in_the_ranking():
    w = attention()
    w.feed("task_failed", thing_id="old", kind="task", label="старе")
    run(w, 6 * 24 * 3600, step=3600)
    w.feed("task_failed", thing_id="new", kind="task", label="нове")
    assert w.hottest() is not None
    assert w.hottest().thing_id == "new", "the oldest thing presses hardest forever"


def test_letting_one_go_by_hand_costs_no_energy():
    w = attention()
    w.feed("task_failed", thing_id="7", kind="task", label="сайт")
    before = w.energy
    w.spoke("7", spent=False)
    assert w.energy == before
    assert w.wants[0].said == 1


def test_the_table_holds_only_what_has_a_producer():
    """A row is a decision, not a vocabulary: each one has an event that
    feeds it. Twelve rows without a producer were cut on 10 November."""
    from heare.engine.attention import APPRAISE

    assert set(APPRAISE) == {
        "task_opened", "task_done", "task_failed", "plan_said",
    }


def test_a_thing_that_stopped_mattering_is_let_go():
    from heare.engine.attention import HELD_AT_MOST

    w = attention()
    w.feed("task_opened", thing_id="ancient", kind="task", label="старе")
    run(w, 10 * 24 * 3600, step=3600)
    for n in range(HELD_AT_MOST + 1):
        w.feed("task_opened", thing_id=f"n{n}", kind="task", label=f"нове {n}")
        run(w, 3600, step=600)
    assert w.thing("ancient") is None, "a released thing stayed in the file"


def test_the_trace_keeps_the_last_fifty():
    w = attention()
    for n in range(60):
        w.feed("task_opened", thing_id=str(n), kind="task", label=f"зроблено {n}")
        w.feed("task_done", thing_id=str(n), kind="task", label=f"зроблено {n}")
        run(w, 600, step=600)
    assert len(w.trace) == 50
    assert "зроблено 59" in w.trace
    assert "зроблено 0" not in w.trace
    assert len(w.things) <= 50
