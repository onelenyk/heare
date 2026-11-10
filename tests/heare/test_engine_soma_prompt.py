"""What presses inside reaches the prompt, and only when there is a soma."""

from __future__ import annotations

from dataclasses import dataclass

from heare.engine import Soma, Engine, Intents
from heare.prompt import persona


@dataclass
class Ev:
    kind: str
    data: dict


def event(status: str = "failed") -> Ev:
    return Ev("task", {"id": 7, "request": "зроби сайт", "news": "Сайт готовий.", "status": status})


class Clock:
    def __init__(self, moment: float = 1000.0) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment

    def go(self, seconds: float) -> None:
        self.moment += seconds


def engine(tmp_path, *, soma=None):
    clock = Clock()
    motor = Engine(Intents(tmp_path / "intents.json"), speak=lambda m: None, now=clock,
                   quiet=("00:00", "00:00"), daily_limit=0, soma=soma)
    return motor, clock


def test_without_a_soma_there_is_nothing_to_say_about_inside(tmp_path):
    motor, _ = engine(tmp_path)
    assert motor.mood() == ""


async def test_with_a_soma_the_prompt_says_what_presses(tmp_path):
    motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event())
    clock.go(3600)
    text = await persona.prompt(mood=motor.mood)
    assert "inside you" in text
    assert "зроби сайт" in text


async def test_without_a_mood_the_prompt_is_as_before():
    text = await persona.prompt()
    assert "inside you" not in text


async def test_an_empty_mood_adds_nothing():
    text = await persona.prompt(mood=lambda: "")
    assert "inside you" not in text


async def test_the_inside_line_carries_no_numbers(tmp_path):
    motor, clock = engine(tmp_path, soma=Soma())
    motor.note(event())
    clock.go(3600)
    inside = motor.mood()
    assert inside
    assert not any(char.isdigit() for char in inside)


def test_the_inside_line_shows_what_presses_first():
    soma = Soma()
    for i in range(1, 4):
        soma.feed("task_opened", thing_id=f"cold{i}", kind="task", label=f"cold {i}")
        want = soma._want("finish", f"cold{i}")
        want.care, want.pressing, want.said = 0.9, 0.0, 3
    soma.feed("task_failed", thing_id="hot", kind="task", label="the hot one")
    inside = soma.state()
    assert inside.index("the hot one") < inside.index("cold 1")
