"""What the agent may do through the panel: show, light, and pins on a yes."""

from __future__ import annotations

import asyncio

from heare.api.registry import Registry
from heare.api.server import _acts
from heare.core.forms import Task
from heare.core.state import State
from heare.language import uk
from heare.tools import Toolbox

ACTS = [{"name": "show", "kind": "show"}, {"name": "led", "kind": "light"},
        {"name": "pin3", "kind": "switch"}]


def _registry(tmp_path, *, state="approved", acts=ACTS):
    sent: list[tuple[str, dict]] = []
    registry = Registry(tmp_path / "parts.json",
                        lambda kind, **data: sent.append((kind, data)))
    registry.attach("oled@desk", "oled", ["output"], "", "desk", state, acts=acts)
    sent.clear()
    return registry, sent


def _toolbox(registry, *, allow=True):
    asked: list[tuple[str, str]] = []
    said: list[str] = []

    async def ask(who, what):
        asked.append((who, what))
        return allow

    box = Toolbox(memory=None, state=State(), delegate=lambda r, c: Task(1, r, ""),
                  stop=lambda: 0, words=uk, has_executors=lambda: False,
                  devices=lambda: registry, ask=ask,
                  say=lambda channel, text: said.append(text))
    return box, asked, said


def _controls(sent):
    return [(d["name"], d["value"]) for kind, d in sent if kind == "control"]


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_no_panel_no_verb(tmp_path):
    registry, _ = _registry(tmp_path, state="pending")
    box, _, _ = _toolbox(registry)
    assert "panel" not in box.verbs() and "read_pin" not in box.verbs()


def test_the_verb_offers_the_pins_the_panel_named(tmp_path):
    registry, _ = _registry(tmp_path)
    box, _, _ = _toolbox(registry)
    schema = box.verbs()["panel"][0]
    assert schema["function"]["parameters"]["properties"]["pin"]["enum"] == ["pin3"]


async def test_showing_and_lighting_need_no_yes(tmp_path):
    registry, sent = _registry(tmp_path)
    box, asked, _ = _toolbox(registry)
    run = box.verbs()["panel"][1]

    await run({"action": "show", "text": "ЧАЙНИК", "seconds": 999})
    await run({"action": "light", "mode": "blink"})

    assert _controls(sent) == [("show", {"text": "ЧАЙНИК", "seconds": 60}),
                               ("led", "blink")]
    assert asked == []


async def test_a_pin_is_switched_only_after_a_yes(tmp_path):
    registry, sent = _registry(tmp_path)
    box, asked, said = _toolbox(registry)

    answer = await box.verbs()["panel"][1]({"action": "switch", "pin": "pin3", "on": True})
    await _settle()

    assert answer == "Питаю дозволу."
    assert asked == [("oled", "увімкнути pin3 на панелі")]
    assert _controls(sent) == [("pin3", True)]
    assert said == ["pin3 увімкнено."]


async def test_without_a_yes_the_pin_is_not_touched(tmp_path):
    registry, sent = _registry(tmp_path)
    box, _, said = _toolbox(registry, allow=False)

    await box.verbs()["panel"][1]({"action": "switch", "pin": "pin3", "on": True})
    await _settle()

    assert _controls(sent) == []
    assert said == ["pin3 не чіпаю: дозволу не було."]


async def test_a_pin_the_panel_did_not_name_is_refused(tmp_path):
    registry, sent = _registry(tmp_path)
    box, asked, _ = _toolbox(registry)

    answer = await box.verbs()["panel"][1]({"action": "switch", "pin": "pin9", "on": True})

    assert answer == "Такого піна на панелі немає."
    assert asked == [] and _controls(sent) == []
    assert registry.act("oled@desk", "pin9", True) is False


async def test_a_pin_is_read_from_what_the_panel_reported(tmp_path):
    registry, _ = _registry(tmp_path)
    box, _, _ = _toolbox(registry)
    read = box.verbs()["read_pin"][1]

    assert await read({"pin": "pin3"}) == "pin3: панель ще не сказала."
    registry.bodies["oled@desk"]["values"]["pin3"] = 1
    assert await read({"pin": "pin3"}) == "pin3: 1"
    assert box.read_only("read_pin")


def test_a_knock_keeps_only_acts_of_known_kinds():
    assert _acts([{"name": "pin3", "kind": "switch"}, {"name": "x", "kind": "shell"},
                  {"name": "", "kind": "show"}, "led", None]) == [
        {"name": "pin3", "kind": "switch"}]
    assert _acts("show") == []
