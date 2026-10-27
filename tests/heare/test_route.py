"""The router: which hands get the work, by a rule and not a model."""

from __future__ import annotations

from heare.core.forms import Task
from heare.model.route import FirstHand, Router
from heare.hand import Handoff


class Named:
    def __init__(self, name, does=None):
        self.name, self.about, self.does = name, "", does
        self.tasks: list[str] = []

    async def run(self, task, desk):
        self.tasks.append(task)
        if self.does is not None:
            return self.does(task)
        return f"{self.name}: готово"


def _router(*tasks: Task) -> Router:
    return Router(None, lambda: {t.id: t for t in tasks})


async def test_the_native_hands_go_first():
    router = _router(Task(1, "блимни", ""))
    assert await router.assign("блимни", [], [Named("opencode"), Named("native-hands")]) == (
        "native-hands", "блимни")


async def test_without_native_hands_the_first_there_is():
    router = _router(Task(1, "x", ""))
    assert (await router.assign("x", [], [Named("opencode")]))[0] == "opencode"


async def test_work_that_goes_on_goes_to_the_hands_that_did_the_last():
    router = _router(Task(1, "напиши тест", "opencode", status="done"),
                     Task(2, "а тепер запусти", "", follows=True))
    assert (await router.assign("а тепер запусти", [],
                                [Named("native-hands"), Named("opencode")]))[0] == "opencode"


async def test_the_task_carries_the_last_turns_of_the_talk():
    router = _router(Task(1, "x", ""))
    _name, task = await router.assign("видали його", [
        {"role": "user", "content": "створи файл a.txt"},
        {"role": "assistant", "content": "Створив."}], [Named("opencode")])
    assert task.startswith("видали його") and "створи файл a.txt" in task


class Desk:
    def __init__(self):
        self.steps = []

    def step(self, text):
        self.steps.append(text)


async def test_what_the_native_hands_hand_on_the_other_hands_do():
    def hand_on(task):
        raise Handoff("Порахуй файли в ~/Downloads")

    cli = Named("opencode")
    first = FirstHand(Named("native-hands", hand_on), lambda: [first, cli])
    desk = Desk()

    assert await first.run("скільки файлів", desk) == "opencode: готово"
    assert cli.tasks == ["Порахуй файли в ~/Downloads"]
    assert desk.steps == ["handed over from native-hands"]


async def test_with_nobody_to_hand_on_to_it_says_so():
    def hand_on(task):
        raise Handoff("x")

    first = FirstHand(Named("native-hands", hand_on), lambda: [first])
    assert "інших рук зараз немає" in await first.run("x", Desk())


async def test_after_a_restart_work_waits_for_native_hands_that_are_coming(monkeypatch):
    import heare.model.route as route

    monkeypatch.setattr(route, "WAIT_S", 2.0)
    here = ["opencode"]
    router = Router(None, lambda: {1: Task(1, "блимни", "")},
                    expected=lambda: True, present=lambda: list(here))
    import asyncio

    asyncio.get_running_loop().call_later(0.3, here.append, "native-hands")
    assert (await router.assign("блимни", [], [Named("opencode")]))[0] == "native-hands"


async def test_native_hands_not_turned_on_are_not_waited_for():
    router = Router(None, lambda: {1: Task(1, "x", "")}, expected=lambda: False)
    assert (await router.assign("x", [], [Named("opencode")]))[0] == "opencode"
