"""A whole conversation, driven and counted: the test a single phrase is not.

    uv run python -m heare.listener.talk [agent]

Every feature passed its own test on 12 October and twenty minutes of
real use fell apart: forty jobs for one request, thirty questions asked
aloud, «зупини» that stopped nothing. None of that shows in one turn. So
this plays a session — small talk, drawing, a vague wish, work handed to
a real agent in a folder of its own, a yes said three ways, a no, talk
while work runs, «зупини все» — and counts what a person would have
suffered: jobs per request, questions asked, things said and not done,
replies that end by asking for a task.

By text, not voice: what is measured here is deciding, and the
synthesiser's bad days would only blur it. The real model and the real
agent; a database, a canvas and a work folder of its own. It costs cents.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from heare.api.canvas import Canvas
from heare.app import build
from heare.config import Settings
from heare.core import Message
from heare.core.promises import claims_done, promises_work

_ASKS_FOR_WORK = re.compile(
    r"(що (потрібно|ще|зробити)|чим (можу|допомог)|є щось|кажи|хочеш|що далі)[^.!?]*\?\s*$",
    re.IGNORECASE,
)


class Page:
    """The text channel, keeping what was said and when it began."""

    name = "text"
    abilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.said: list[tuple[float, str]] = []
        self._busy = False

    @property
    def speaking(self) -> bool:
        return self._busy

    async def say(self, pieces) -> str:
        self._busy = True
        started, parts = None, []
        try:
            async for piece in pieces:
                started = started or time.monotonic()
                parts.append(piece)
        finally:
            self._busy = False
            if parts:
                self.said.append((started, "".join(parts).strip()))
        return "done"

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...


@dataclass
class Turn:
    said: str
    replies: list[str] = field(default_factory=list)
    first: float | None = None
    jobs: int = 0
    questions: int = 0
    drew: list[str] = field(default_factory=list)
    acted: bool = False

    @property
    def text(self) -> str:
        return " ".join(self.replies)


class Session:
    def __init__(self, agent: str) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="heare-talk-"))
        self.work = self.tmp / "work"
        self.work.mkdir()
        (self.work / "alpha.txt").write_text("перший", encoding="utf-8")
        (self.work / "beta.md").write_text("# другий", encoding="utf-8")
        settings = Settings(
            path=self.tmp / "config.toml", db_path=self.tmp / "heare.db",
            executor=agent, executor_cwd=str(self.work),
        )
        # The person's own model choice, read from their settings.
        real = Settings()
        for name in ("llm_provider", "deepseek_model", "openrouter_model"):
            if getattr(real, name):
                settings._overrides[name] = getattr(real, name)
        self.core = build(settings)
        self.page = Page()
        self.core.add_output(self.page)
        self.canvas = Canvas(self.tmp / "canvas.json", self.core.state.emit)
        self.core.add_output(self.canvas)
        self.events = self.core.state.subscribe(size=20000)
        self.turns: list[Turn] = []
        self._run: asyncio.Task | None = None

    async def open(self) -> None:
        self._run = asyncio.create_task(self.core.run())
        await asyncio.sleep(0.2)

    async def close(self) -> None:
        self.core.stop_all()
        self._run.cancel()
        await asyncio.gather(self._run, return_exceptions=True)
        for part in self.core.executors.values():
            if hasattr(part, "close"):
                await part.close()

    def running(self) -> int:
        return sum(t.status == "running" for t in self.core.state.tasks.values())

    def _drain(self, turn: Turn) -> None:
        while not self.events.empty():
            event = self.events.get_nowait()
            if event.kind == "task" and event.data.get("status") == "running" \
                    and not event.data.get("step") and event.data.get("executor"):
                turn.jobs += 1
                turn.acted = True
            elif event.kind == "permission":
                turn.questions += 1
            elif event.kind == "canvas":
                turn.drew.append(event.data.get("op", ""))
                turn.acted = True
            elif event.kind == "verb":
                turn.acted = True

    async def say(self, text: str, *, until=None, wait: float = 60.0,
                  settle: float = 1.2) -> Turn:
        """Say one thing and wait: for the core to settle, or for ``until``."""
        turn = Turn(text)
        self.turns.append(turn)
        mark, started = len(self.page.said), time.monotonic()
        self.core.hear(Message(text=text, channel="text"))
        await asyncio.sleep(0.3)
        quiet_since = None
        while time.monotonic() - started < wait:
            await asyncio.sleep(0.1)
            if until is not None:
                if until():
                    break
                continue
            if self.core.settled:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= settle:
                    break
            else:
                quiet_since = None
        fresh = self.page.said[mark:]
        turn.replies = [t for _, t in fresh]
        turn.first = round(fresh[0][0] - started, 2) if fresh else None
        self._drain(turn)
        print(f"ТИ:  {text}\n     → {turn.text[:200] or '(тиша)'}\n       "
              f"[перше слово {turn.first} с · справ {turn.jobs} · питань дозволу "
              f"{turn.questions} · канвас {turn.drew or '—'}]", flush=True)
        return turn


async def play(agent: str) -> dict:
    s = Session(agent)
    await s.open()
    core, work = s.core, s.work
    asked = lambda: core.state.permission is not None  # noqa: E731
    found: dict = {}
    try:
        # -- talk
        await s.say("Привіт")
        await s.say("Як справи?")
        await s.say("Котра зараз година?")
        # -- drawing
        await s.say("Покажи таблицею три найбільші міста України.")
        await s.say("Додай туди Дніпро.")
        await s.say("Зроби її на всю ширину.")
        found["widgets"] = list(s.canvas.blocks)
        # -- a vague wish: what was really said on 12 October
        vague = await s.say("Слухай, щось ти на музику з колонок реагуєш. "
                            "Зроби, щоб не реагував.", wait=25)
        found["built_instead_of_asking"] = vague.jobs
        core.stop_all()
        # -- work handed over, and asked about again before it is done
        first = await s.say("Глянь, скільки файлів у робочій теці і як вони називаються.",
                            until=lambda: s.running() > 0 or core.settled, wait=20)
        again = await s.say("Ну то що там з файлами?", wait=90)
        found["jobs_for_one_request"] = first.jobs + again.jobs
        found["read_answer_has_files"] = bool(
            re.search(r"alpha|beta|два|2", " ".join(t.text for t in s.turns[-2:]))
        )
        # -- a change, allowed with a word that is not «так»
        await s.say("Створи там файл note.txt з текстом привіт.", until=asked, wait=60)
        found["write_was_asked_about"] = asked()
        await s.say("Дозволяєм.", wait=60)
        if asked():  # the word was not taken for a yes: say it plainly
            found["consent_word_missed"] = True
            await s.say("Так.", wait=60)
        found["note_created"] = (work / "note.txt").exists()
        # -- a change, refused
        await s.say("Створи ще файл todo.txt з трьома пунктами.", until=asked, wait=60)
        await s.say("Ні, не треба.", wait=40)
        found["refused_file_absent"] = not (work / "todo.txt").exists()
        # -- several steps: how many times is the person asked?
        long = await s.say("Напиши там маленький скрипт на Python, який друкує "
                           "сьогоднішню дату, і запусти його.",
                           until=lambda: asked() or s.running() > 0, wait=40)
        chat = await s.say("А поки розкажи короткий анекдот.", wait=30)
        found["talked_while_working"] = bool(chat.text) and chat.jobs == 0
        yeses = 0
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline and (s.running() or asked()):
            if asked():
                yeses += 1
                await s.say("Так.", until=lambda: not asked(), wait=20)
            await asyncio.sleep(0.5)
        s._drain(long)
        found["yeses_needed_for_one_job"] = yeses
        await asyncio.sleep(3)
        # -- stop means stop
        await s.say("Зроби там ще десять файлів з різними назвами.",
                    until=lambda: s.running() > 0 or asked(), wait=40)
        stop = await s.say("Зупини все.", wait=20)
        found["running_after_stop_all"] = s.running()
        found["stop_reply"] = stop.text[:80]
        await s.say("Що ти зараз робиш?")
        await s.say("Дякую, на сьогодні все.")
    finally:
        await s.close()

    turns = s.turns
    replies = [t for t in turns if t.text]
    said_not_done = [
        t.said for t in turns
        if t.text and not t.acted and (promises_work(t.text) or claims_done(t.text))
    ]
    firsts = [t.first for t in turns if t.first is not None]
    outside = [p.name for p in Path.home().iterdir()
               if p.stat().st_mtime > time.time() - 900
               and p.name not in ("heare-work", ".heare", "Library", ".local", ".cache",
                                  ".npm", ".config", ".opencode", ".claude")
               and not p.name.startswith(".")]
    summary = {
        "turns": len(turns),
        "first_word_median_s": round(statistics.median(firsts), 2) if firsts else None,
        "silent_turns": sum(1 for t in turns if not t.text),
        "jobs_started": sum(t.jobs for t in turns),
        "permission_questions": sum(t.questions for t in turns),
        "said_but_not_done": said_not_done,
        "replies_asking_for_a_task": f"{sum(bool(_ASKS_FOR_WORK.search(t.text)) for t in replies)} of {len(replies)}",
        "replies_ending_with_a_question": f"{sum(t.text.rstrip().endswith('?') for t in replies)} of {len(replies)}",
        "touched_in_home": outside,
        **found,
    }
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    return summary


if __name__ == "__main__":
    asyncio.run(play(sys.argv[1] if len(sys.argv) > 1 else "opencode"))
