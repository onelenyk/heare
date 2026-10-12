"""Two readings of a sentence made in code, not by the model: a turn that
is a command to stop, and a reply that promises work instead of doing it."""

from __future__ import annotations

import pytest

from heare.language.uk import is_halt
from heare.language.promises import promises_work

NAMES = frozenset({"дока", "доко", "докер"})


@pytest.mark.parametrize(
    ("turn", "halt"),
    [
        ("Дока, стоп", True),
        ("стоп", True),
        ("зупинись, зупинись", True),
        ("Докер, скасуй", True),
        # Into silence «ні» answers a question; «тихо» is about the voice.
        ("ні", False),
        ("тихо", False),
        # A request that happens to start with the word is not the word.
        ("Дока, стоп відтворення музики", False),
        ("", False),
    ],
)
def test_a_stop_is_the_whole_turn(turn: str, halt: bool) -> None:
    assert is_halt(turn, NAMES) is halt


@pytest.mark.parametrize(
    ("reply", "promise"),
    [
        # Seen live on 27 September, each with no tool call beside it.
        ("Гляну, що там навантажує систему. Секунду.", True),
        ("Ставлю нагадування на десять хвилин — про воду.", True),
        ("Відкриваю браузер, зараз буде.", True),
        ("Дивлюся, що там у тебе на екрані.", True),
        ("Нагадаю за десять хвилин про воду.", True),
        # Answers, not promises.
        ("Сьогодні п'ятниця.", False),
        ("Перевірила: найбільше їсть Chrome.", False),
        ("Чую.", False),
        # Long enough to be an answer that mentions checking later.
        ("Я перевірю це завтра зранку, а поки що скажу так: найімовірніше, "
         "проблема в налаштуваннях мережі, бо решта працює як слід.", False),
    ],
)
def test_a_promise_is_short_and_about_to_act(reply: str, promise: bool) -> None:
    assert promises_work(reply) is promise
