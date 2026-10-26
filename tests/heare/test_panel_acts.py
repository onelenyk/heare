"""What the native hand may do through the panel: show, light, pins on the judge."""

from __future__ import annotations

from heare.api.registry import Registry
from heare.api.server import _acts
from heare.native_hands import NativeHand
from heare.tools import Toolbox
from heare.core.forms import Task
from heare.core.state import State
from heare.language import uk
from tests.heare.fakes import Says, Script

ACTS = [{"name": "show", "kind": "show"}, {"name": "led", "kind": "light"},
        {"name": "pin3", "kind": "switch"}]


class Desk:
    follows, asked = False, ""

    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.asked_for: list[tuple[str, str]] = []

    def step(self, text: str) -> None:
        pass

    async def permit(self, what: str, kind: str = "change", detail: str = "") -> bool:
        self.asked_for.append((what, kind))
        return self.allow


def _registry(tmp_path, *, state="approved"):
    sent: list[tuple[str, dict]] = []
    registry = Registry(tmp_path / "parts.json",
                        lambda kind, **data: sent.append((kind, data)))
    registry.attach("oled@desk", "oled", ["output"], "", "desk", state, acts=ACTS)
    sent.clear()
    return registry, sent


def _controls(sent):
    return [(d["name"], d["value"]) for kind, d in sent if kind == "control"]


class Door:
    """The brain's acts doors, without the HTTP."""

    def __init__(self, registry: Registry) -> None:
        self._registry = registry

    async def acting(self) -> dict[str, dict]:
        return self._registry.acting()

    async def act(self, pid: str, name: str, value: object) -> bool:
        return self._registry.act(pid, name, value)


def _hand(registry, *answers):
    model = Script()
    model.will_say(*answers)
    return NativeHand(model, Door(registry)), model


async def test_showing_and_lighting_are_done_without_asking(tmp_path):
    registry, sent = _registry(tmp_path)
    hand, model = _hand(registry, Says(calls=(
        ("show", {"text": "hello", "seconds": 999}), ("light", {"mode": "blink"}))),
        "Показав і блимаю.")
    desk = Desk()

    said = await hand.run("покажи hello і блимни", desk)

    assert said == "Показав і блимаю."
    assert _controls(sent) == [("show", {"text": "hello", "seconds": 60}),
                               ("led", "blink")]
    assert desk.asked_for == []
    assert set(model.offered[0]) == {"show", "light", "switch", "read_pin"}


async def test_a_pin_goes_through_the_judge(tmp_path):
    registry, sent = _registry(tmp_path)
    hand, _ = _hand(registry, Says(calls=(("switch", {"pin": "pin3", "on": True}),)),
                    "Увімкнув.")
    desk = Desk()

    await hand.run("увімкни pin3", desk)

    assert desk.asked_for == [("увімкнути pin3 на панелі", "judge")]
    assert _controls(sent) == [("pin3", True)]


async def test_without_a_yes_the_pin_is_not_touched(tmp_path):
    registry, sent = _registry(tmp_path)
    hand, model = _hand(registry, Says(calls=(("switch", {"pin": "pin3", "on": True}),)),
                        "Не вмикаю.")

    await hand.run("увімкни pin3", Desk(allow=False))

    assert _controls(sent) == []
    assert model.asked[1][-1]["content"] == "pin3 не чіпаю: дозволу немає."


async def test_a_pin_the_panel_did_not_name_is_refused(tmp_path):
    registry, sent = _registry(tmp_path)
    hand, model = _hand(registry, Says(calls=(("switch", {"pin": "pin9", "on": True}),)),
                        "Немає.")
    desk = Desk()

    await hand.run("увімкни pin9", desk)

    assert desk.asked_for == [] and _controls(sent) == []
    assert model.asked[1][-1]["content"] == "Такого піна немає."
    assert registry.act("oled@desk", "pin9", True) is False


async def test_a_pin_is_read_from_what_the_panel_reported(tmp_path):
    registry, _ = _registry(tmp_path)
    registry.bodies["oled@desk"]["values"]["pin3"] = 1
    hand, model = _hand(registry, Says(calls=(("read_pin", {"pin": "pin3"}),)), "Одиниця.")

    await hand.run("що на pin3", Desk())

    assert model.asked[1][-1]["content"] == "pin3: 1"


async def test_with_no_part_here_it_says_so_without_the_model(tmp_path):
    registry, _ = _registry(tmp_path, state="pending")
    hand, model = _hand(registry)

    assert await hand.run("увімкни світло", Desk()) == "Жодної частини з діями зараз немає."
    assert model.asked == []


def test_the_voice_has_no_verbs_of_its_own_for_the_panel():
    box = Toolbox(memory=None, state=State(), delegate=lambda r, c: Task(1, r, ""),
                  stop=lambda: 0, words=uk, has_executors=lambda: True)
    assert not {"panel", "read_pin", "show", "switch"} & set(box.verbs())


def test_a_knock_keeps_only_acts_of_known_kinds():
    assert _acts([{"name": "pin3", "kind": "switch"}, {"name": "x", "kind": "shell"},
                  {"name": "", "kind": "show"}, "led", None]) == [
        {"name": "pin3", "kind": "switch"}]
    assert _acts("show") == []
