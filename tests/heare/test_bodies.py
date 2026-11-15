"""The bodies: one list, one call, the same for the hands and the command line."""

from __future__ import annotations

from types import SimpleNamespace

from heare import bodies

SHOW = {"name": "show", "about": "Show words", "effect": "harmless",
        "params": {"type": "object", "properties": {"text": {"type": "string"}}}}
PIN = {"name": "pin", "about": "A relay", "effect": "change",
       "params": {"type": "object", "properties": {"on": {"type": "boolean"}}}}
PICTURE = {"name": "image", "about": "Draw", "effect": "harmless",
           "params": {"type": "object", "properties": {"svg": {"type": "string"}}},
           "render": {"svg": {"to": "mono", "width": 72, "height": 40, "as": "bitmap"}}}
DOT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 40">'
       '<circle cx="36" cy="20" r="12" fill="white"/></svg>')


def part(name: str = "desk", *skills: dict, about: str = "") -> dict:
    return {"channel": f"oled-{name}", "name": name, "about": about,
            "skills": list(skills), "values": {}}


class Devices:
    def __init__(self, parts: dict, answer: dict | None = None) -> None:
        self.parts, self.calls = parts, []
        self.answer = answer or {"ok": True, "result": "done"}

    async def skilled(self) -> dict:
        return self.parts

    async def call(self, pid: str, name: str, args: dict) -> dict:
        self.calls.append((pid, name, args))
        return self.answer


class Permit:
    def __init__(self, allow: bool = True) -> None:
        self.allow, self.asked = allow, []

    async def __call__(self, what: str, kind: str, detail: str) -> bool:
        self.asked.append(kind)
        return self.allow


def test_the_list_names_every_pid_and_skill():
    text = bodies.describe({"oled-desk@sim": part("desk", SHOW, PIN, about="на столі")})
    assert "oled-desk@sim" in text and "show(text)" in text and "[change]" in text
    assert "на столі" in text


def test_an_empty_list_says_so():
    assert bodies.describe({}) == "no bodies are here"


def test_a_skill_is_found_by_pid_and_name():
    skilled = {"oled-desk@a": part("desk", SHOW), "oled-desk@b": part("desk", PIN)}
    assert bodies.find(skilled, "oled-desk@b", "pin") is PIN
    assert bodies.find(skilled, "oled-desk@b", "show") is None
    assert bodies.find(skilled, "nobody", "show") is None


async def test_a_harmless_skill_asks_nothing():
    devices, permit = Devices({"p": part()}, {"ok": True, "result": "shown"}), Permit()
    assert await bodies.call(devices, "p", SHOW, {"text": "hi"}, permit) == "shown"
    assert permit.asked == []


async def test_a_change_skill_asks_the_judge():
    devices, permit = Devices({"p": part()}), Permit()
    await bodies.call(devices, "p", PIN, {"on": True}, permit)
    assert permit.asked == ["judge"] and devices.calls == [("p", "pin", {"on": True})]


async def test_a_refused_leave_does_not_reach_the_part():
    devices, permit = Devices({"p": part()}), Permit(allow=False)
    said = await bodies.call(devices, "p", PIN, {"on": True}, permit)
    assert said.startswith("Not done") and devices.calls == []


async def test_the_part_s_render_rule_is_applied_before_the_call():
    devices = Devices({"p": part()})
    await bodies.call(devices, "p", PICTURE, {"svg": DOT}, Permit())
    (_pid, _name, args), = devices.calls
    assert "svg" not in args and len(args["bitmap"]) > 0


async def test_a_part_that_did_not_answer_is_said_so():
    devices = Devices({"p": part()}, {"ok": False, "result": "no answer"})
    assert await bodies.call(devices, "p", SHOW, {"text": "x"}, Permit()) == "Failed: no answer"


async def test_a_change_without_a_job_is_refused(capsys):
    parts = Devices({"p": part("desk", PIN)})
    code = await bodies._call(parts, None, SimpleNamespace(part="p", skill="pin", args="{}"))
    assert code == 1 and "needs a job" in capsys.readouterr().out
    assert parts.calls == []


async def test_a_read_without_a_job_is_printed_as_ok(capsys):
    parts = Devices({"p": part("desk", SHOW)})
    code = await bodies._call(
        parts, None, SimpleNamespace(part="p", skill="show", args='{"text": "x"}'))
    assert code == 0 and "ok: done" in capsys.readouterr().out
