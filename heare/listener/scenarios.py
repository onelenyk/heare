"""What the listener asks, and the numbers it brings back.

The same questions for whichever app is on the other side of the tape,
so "no worse than the old one" is a comparison of two rows and not an
impression.
"""

from __future__ import annotations

import asyncio
import json
import statistics

from heare.listener import ear
from heare.listener.tape import Tape

QUESTIONS = (
    ("Дока, скільки буде два плюс два?", ("чотири", "4")),
    ("Дока, яка столиця Франції?", ("париж",)),
    ("А Італії?", ("рим",)),
)
LONG = "Дока, розкажи докладно, на кілька речень, про історію Києва."
STOP = "Дока, стоп."
NOD = "Ага."


async def run(tape: Tape, rounds: int = 2) -> dict:
    failed = 0
    firsts: list[float] = []
    for _ in range(rounds):
        for question, expect in QUESTIONS:
            answer = await ear.ask(tape, question)
            if not answer.has(*expect):
                answer.problems.append(f"у відповіді немає {expect}")
            print(answer.line(), flush=True)
            failed += bool(answer.problems)
            if answer.first_sound is not None:
                firsts.append(answer.first_sound)
            await asyncio.sleep(1.0)

    # Talked over: how long until it is quiet, and what it says next.
    cut = await ear.cut_in(tape, LONG, STOP)
    print(cut.line(), flush=True)
    summary = {
        "first_sound_median_s": round(statistics.median(firsts), 2) if firsts else None,
        "first_sound_all_s": [round(f, 2) for f in firsts],
        "answers_with_problems": failed,
        "answers": len(QUESTIONS) * rounds,
        "quiet_after_interruption_s": (
            None if cut.quiet_after is None else round(cut.quiet_after, 2)
        ),
        "said_after_interruption": cut.then_said,
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary
