"""What a part can do, as the part itself says it — and a call that gets an answer.

A part brings its skills in its knock: a name, a line about it, its
parameters as a JSON schema, and what it does to the world (``effect``).
The brain checks the *form* of a skill and nothing of its meaning: a new
sensor is a new skill in the part, never a change here. The hands take the
skills as tools as they are.

``effect`` is for leave: ``read`` and ``harmless`` are done at once,
``change`` goes through the judge. The person vouches for a part's honesty
about it when they let the part in; a part whose ``change`` skills are not
what was let in is asked about again (``Trust``).

A call is delivered as a ``call`` event to the part and waits for the part's
answer (``Calls``).
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import re
from pathlib import Path

EFFECTS = ("read", "harmless", "change")
MAX_SKILLS = 32
MAX_ABOUT = 300
MAX_PARAMS = 2048
NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CALL_S = 5.0


def checked(raw: object) -> list[dict]:
    """The skills a knock offers, those of a right form; the rest dropped."""
    if not isinstance(raw, list):
        return []
    found: list[dict] = []
    for skill in raw[:MAX_SKILLS]:
        if not isinstance(skill, dict):
            continue
        name, about = skill.get("name"), skill.get("about")
        params = skill.get("params") or {"type": "object", "properties": {}}
        if not (isinstance(name, str) and NAME.match(name)):
            continue
        if not isinstance(about, str) or not about.strip():
            continue
        if skill.get("effect") not in EFFECTS:
            continue
        if not isinstance(params, dict) or params.get("type") != "object":
            continue
        if len(json.dumps(params, ensure_ascii=False)) > MAX_PARAMS:
            continue
        if name in {s["name"] for s in found}:
            continue
        found.append({"name": name, "about": about.strip()[:MAX_ABOUT],
                      "params": params, "effect": skill["effect"]})
    return found


def fingerprint(skills: list[dict]) -> str:
    """What the person vouched for: the skills that change the world."""
    changing = sorted((s["name"], json.dumps(s["params"], sort_keys=True))
                      for s in skills if s["effect"] == "change")
    return hashlib.sha256(json.dumps(changing).encode()).hexdigest()[:16]


class Trust:
    """Which ``change`` skills each part was let in with."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, str]:
        try:
            found = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def same(self, pid: str, skills: list[dict]) -> bool:
        """True when nothing that changes the world is new since the person's yes.

        A part trusted before skills were kept has nothing written yet: its
        skills are written now, as the trust it already had."""
        known = self._read()
        if pid not in known:
            self.keep(pid, skills)
            return True
        return known[pid] == fingerprint(skills)

    def keep(self, pid: str, skills: list[dict]) -> None:
        known = self._read()
        known[pid] = fingerprint(skills)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(known, ensure_ascii=False, sort_keys=True),
                              encoding="utf-8")


class Calls:
    """Calls sent to parts, waiting for their answers."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._waiting: dict[int, tuple[str, asyncio.Future]] = {}

    def open(self, pid: str) -> tuple[int, asyncio.Future]:
        call_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._waiting[call_id] = (pid, future)
        return call_id, future

    async def wait(self, call_id: int, future: asyncio.Future,
                   seconds: float | None = None) -> dict:
        try:
            return await asyncio.wait_for(asyncio.shield(future),
                                          CALL_S if seconds is None else seconds)
        except TimeoutError:
            return {"ok": False, "result": "частина не відповіла"}
        finally:
            self._waiting.pop(call_id, None)

    def answer(self, call_id: int, pid: str, ok: bool, result: object) -> bool:
        """The part's answer; False when no call of that id waits for that part."""
        waiting = self._waiting.get(call_id)
        if waiting is None or waiting[0] != pid or waiting[1].done():
            return False
        waiting[1].set_result({"ok": bool(ok), "result": result})
        return True
