"""Screens the person asks for, written on the spot and kept as their files.

The built-in page is deliberately small. Everything else — costs, a
history view, a form for some setting — the person asks for, and it is
written against the same API the built-in page uses. A screen is saved
in the person's own folder; the assistant's code does not change.

In the core's terms this is an executor like any other: it is handed a
task and returns what came of it, so the model that hands work out
chooses it the way it chooses an agent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Awaitable, Callable

from heare.core import Desk

API = """\
The page is served by the assistant and may call its API. Every request
needs the header "X-Heare-Token": window.HEARE_TOKEN (already defined).

GET  /api/state            -> {phase: idle|hearing|thinking|speaking, parts:{inputs,outputs,executors}, tasks:[...], permission, controls}
GET  /api/controls         -> {part: {name: value}}, e.g. {"voice": {"mic": true, "speaker": true, "volume": 1.0, "listen": "judge"}}
POST /api/controls         {part, name, value}   turn one (mic/speaker: bool, volume: 0..1, listen: "judge"|"name")
POST /api/stop                              stop the reply and all work now
GET  /api/tasks            -> [{id, request, executor, status, step, result, started, ended}]
POST /api/tasks            {request}        start a piece of work
POST /api/tasks/{id}/stop                   stop it
POST /api/permission/{id}  {allow: bool}    answer a pending question
POST /api/message          {text}           say something to the assistant
GET  /api/events?token=…   Server-Sent Events: {kind, at, ...}; kinds: heard{text,channel},
                           reply{text}, said{text,channel}, phase{phase}, task{...},
                           permission{id,executor,what}, usage{...}
GET  /api/settings         -> plain settings (never keys)
POST /api/settings         {name: value}    change settings
GET  /api/keys             -> {KEY_NAME: true|false}  which keys exist
POST /api/keys             {name, value}    save a key (it can never be read back)
GET  /api/memory?q=…       -> {said}        search past conversations
GET  /api/screens          -> [names]
"""

_WRITE = (
    "You write one self-contained HTML page for a person's own assistant. "
    "Inline CSS and JavaScript only, no external resources. Ukrainian text. "
    "Clean and readable in light and dark (prefers-color-scheme), works on a "
    "narrow window. Use only the API below; if what is asked needs data the "
    "API does not have, show that plainly on the page instead of inventing "
    "numbers.\n\n" + API + "\n"
    "Answer with exactly this and nothing else:\n"
    "name: <short-latin-name-with-dashes>\n<!doctype html>…the page…"
)
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,60}")


class ScreenMaker:
    name = "screens"
    about = ("робить для людини екран (сторінку): показати стан, справи, "
             "налаштування, форму чи таблицю з даних самого асистента")

    def __init__(self, folder: Path, write: Callable[[str, str], Awaitable[str]],
                 emit: Callable[..., object]) -> None:
        """write: (system, request) -> the model's answer. emit: the
        core's event, so a watching page lists the new screen at once."""
        self._folder = Path(folder)
        self._write = write
        self._emit = emit

    async def run(self, task: str, desk: Desk) -> str:
        desk.step("пишу екран")
        answer = (await self._write(_WRITE, task)).strip()
        answer = re.sub(r"^```[a-z]*\n|\n```$", "", answer)
        first, _, html = answer.partition("\n")
        name = first.removeprefix("name:").strip().lower()
        if not _NAME.fullmatch(name) or "<" not in html:
            self._emit("cannot", what=task[:120], why="the model did not return a page")
            return "Не вдалося: модель не повернула сторінку."
        self._folder.mkdir(parents=True, exist_ok=True)
        (self._folder / f"{name}.html").write_text(html.strip(), encoding="utf-8")
        self._emit("screen", name=name)
        return f"Екран «{name}» готовий, він у переліку твоїх екранів."
