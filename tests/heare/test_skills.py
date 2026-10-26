"""Skills: a part's own words for what it can do, checked for form only."""

from __future__ import annotations

import asyncio

from heare.api import skills
from heare.native_hands import NativeHand, catalog
from tests.heare.fakes import Says, Script

SHOW = {"name": "show", "about": "Показати слова", "effect": "harmless",
        "params": {"type": "object", "properties": {"text": {"type": "string"}}}}
PIN = {"name": "pin3", "about": "Реле лампи", "effect": "change",
       "params": {"type": "object", "properties": {"on": {"type": "boolean"}}}}
HEAT = {"name": "temperature", "about": "Температура, °C", "effect": "read",
        "params": {"type": "object", "properties": {}}}


def test_a_skill_of_any_meaning_and_the_right_form_is_kept():
    weird = {"name": "smell", "about": "Чи пахне кавою", "effect": "read",
             "params": {"type": "object", "properties": {}}}
    assert skills.checked([SHOW, weird]) == [SHOW, weird]


def test_skills_of_a_wrong_form_are_dropped():
    bad = [
        {**SHOW, "name": "Show me"},          # not a name
        {**SHOW, "name": "x", "about": " "},  # no words
        {**SHOW, "name": "y", "effect": "explode"},
        {**SHOW, "name": "z", "params": {"type": "string"}},
        {**SHOW, "name": "w", "params": {"type": "object", "x": "a" * 5000}},
        SHOW, SHOW,                            # the second of one name
        "show", None,
    ]
    assert [s["name"] for s in skills.checked(bad)] == ["show"]
    assert skills.checked("show") == []


def test_a_skill_may_ask_its_parameter_rendered_in_a_known_form_only():
    image = {"name": "image", "about": "Картинка", "effect": "harmless",
             "params": {"type": "object", "properties": {"svg": {"type": "string"}}}}
    good = {**image, "render": {"svg": {"to": "mono", "width": 72, "height": 40, "as": "bitmap"}}}
    assert skills.checked([good])[0]["render"]["svg"]["as"] == "bitmap"
    for bad in ({"svg": {"to": "jpeg", "width": 72, "height": 40}},
                {"svg": {"to": "mono", "width": 9999, "height": 40}},
                {"photo": {"to": "mono", "width": 72, "height": 40}},
                {"svg": {"to": "mono", "width": 72, "height": 40, "as": "Not A Name"}},
                "mono"):
        assert skills.checked([{**image, "render": bad}]) == []


def test_deep_render_rules_are_checked_for_form():
    scene = {"name": "screen", "about": "Сцена", "effect": "harmless",
             "params": {"type": "object", "properties": {"scene": {"type": "object"}}}}
    rule = {"deep": True, "key": "svg", "where": {"widget": "icon"}, "to": "mono",
            "width": 16, "height": 16, "as": "bits"}
    kept = skills.checked([{**scene, "render": {"scene": [rule]}}])
    assert kept[0]["render"]["scene"][0]["where"] == {"widget": "icon"}
    for bad in ([], [{**rule, "key": "Bad Key"}], [{**rule, "where": {"a": 1}}],
                [rule] * 5 + [{**rule, "to": "jpeg"}]):
        assert skills.checked([{**scene, "render": {"scene": bad}}]) == [], bad


def test_a_new_world_changing_skill_is_a_new_trust(tmp_path):
    trust = skills.Trust(tmp_path / "part_skills.json")
    assert trust.same("oled@desk", [SHOW, PIN]) is True      # first time: kept
    assert trust.same("oled@desk", [SHOW, PIN, HEAT]) is True  # a reading is no new trust
    pin4 = {**PIN, "name": "pin4"}
    assert trust.same("oled@desk", [SHOW, PIN, pin4]) is False
    trust.keep("oled@desk", [SHOW, PIN, pin4])
    assert trust.same("oled@desk", [SHOW, PIN, pin4]) is True


async def test_a_call_gets_its_part_s_answer_and_nobody_else_s():
    calls = skills.Calls()
    call_id, waiting = calls.open("oled@desk")
    assert calls.answer(call_id, "oled@kitchen", True, 1) is False
    assert calls.answer(call_id, "oled@desk", True, 21.5) is True
    assert await calls.wait(call_id, waiting) == {"ok": True, "result": 21.5}


async def test_a_call_nobody_answers_says_so():
    calls = skills.Calls()
    call_id, waiting = calls.open("oled@desk")
    assert await calls.wait(call_id, waiting, 0.01) == {"ok": False,
                                                        "result": "частина не відповіла"}


def _part(name, *own, about=""):
    return {"channel": f"oled-{name}", "name": name, "about": about,
            "skills": list(own), "values": {}}


def test_every_part_s_skills_are_tools_named_for_their_part():
    tools = catalog({"oled-desk@sim": _part("desk", SHOW, about="на столі"),
                     "oled-kitchen@sim": _part("kitchen", SHOW, HEAT)})
    assert set(tools) == {"desk__show", "kitchen__show", "kitchen__temperature"}
    schema = tools["desk__show"].schema()["function"]
    assert schema["description"] == "desk (на столі): Показати слова"
    assert schema["parameters"] == SHOW["params"]


class Devices:
    def __init__(self, parts, answer=None):
        self.parts, self.calls = parts, []
        self.answer = answer or {"ok": True, "result": "зроблено"}

    async def skilled(self):
        return self.parts

    async def call(self, pid, name, args):
        self.calls.append((pid, name, args))
        return self.answer


class Desk:
    follows, asked, stopped = False, "", False

    def __init__(self, allow=True, says="") -> None:
        self.allow, self.says = allow, says
        self.permits, self.questions = [], []

    def step(self, text):
        pass

    async def permit(self, what, kind="change", detail=""):
        self.permits.append(kind)
        return self.allow

    async def ask(self, text):
        self.questions.append(text)
        return self.says


def _hand(devices, *answers):
    model = Script()
    model.will_say(*answers)
    return NativeHand(model, devices), model


async def test_a_reading_and_a_harmless_skill_need_no_leave():
    devices = Devices({"oled-desk@sim": _part("desk", SHOW, HEAT)}, {"ok": True, "result": 21.5})
    hand, model = _hand(devices, Says(calls=(("desk__temperature", {}),
                                             ("desk__show", {"text": "21"}))), "21,5 градуса.")
    desk = Desk()

    assert await hand.run("яка температура, і покажи", desk) == "21,5 градуса."
    assert devices.calls == [("oled-desk@sim", "temperature", {}),
                             ("oled-desk@sim", "show", {"text": "21"})]
    assert desk.permits == []
    assert model.asked[1][-2]["content"] == "21.5"


async def test_a_world_changing_skill_goes_through_the_judge():
    devices = Devices({"oled-desk@sim": _part("desk", PIN)})
    hand, _ = _hand(devices, Says(calls=(("desk__pin3", {"on": True}),)), "Так.")
    desk = Desk(allow=False)

    await hand.run("увімкни лампу", desk)

    assert desk.permits == ["judge"] and devices.calls == []


async def test_twins_of_one_name_are_asked_about():
    devices = Devices({"oled-desk@a": _part("desk", SHOW), "oled-desk@b": _part("desk", SHOW)})
    hand, _ = _hand(devices, Says(calls=(("desk__show", {"text": "hi"}),)), "Показав.")
    desk = Desk(says="другу, 2")

    await hand.run("покажи hi", desk)

    assert "oled-desk@a" in desk.questions[0] and "oled-desk@b" in desk.questions[0]
    assert devices.calls == [("oled-desk@b", "show", {"text": "hi"})]


async def test_a_part_that_did_not_answer_is_not_said_to_have_done_it():
    devices = Devices({"oled-desk@sim": _part("desk", SHOW)},
                      {"ok": False, "result": "частина не відповіла"})
    hand, model = _hand(devices, Says(calls=(("desk__show", {"text": "hi"}),)), "Не вийшло.")

    await hand.run("покажи hi", Desk())

    assert model.asked[1][-1]["content"] == "Не вийшло: частина не відповіла"


async def test_what_is_not_theirs_the_native_hands_hand_on_as_worded():
    from heare import hand as hand_life

    hand, model = _hand(Devices({}), Says(calls=(
        ("pass_to_cli_hands", {"task": "Порахуй файли в ~/Downloads"}),)))
    try:
        await hand.run("скільки файлів у завантаженнях", Desk())
    except hand_life.Handoff as handoff:
        assert handoff.task == "Порахуй файли в ~/Downloads"
    else:
        raise AssertionError("no handoff")
    assert model.offered[0] == ["pass_to_cli_hands"]


async def test_out_of_steps_it_says_what_was_done():
    devices = Devices({"oled-desk@sim": _part("desk", SHOW)}, {"ok": True, "result": "ok"})
    turn = Says(calls=(("desk__show", {"text": "a"}),))
    hand, _ = _hand(devices, *[turn] * 4)
    assert await hand.run("показуй різне", Desk()) == "Зроблено: ok ok ok"


async def test_a_stop_ends_the_work_before_the_next_call():
    devices = Devices({"oled-desk@sim": _part("desk", SHOW)})
    hand, _ = _hand(devices, Says(calls=(("desk__show", {"text": "a"}),)))
    desk = Desk()
    desk.stopped = True
    try:
        await hand.run("покажи", desk)
    except asyncio.CancelledError:
        pass
    assert devices.calls == []
