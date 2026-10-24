"""The clock's exam: kill every process of the live heare in turn and see
that everything comes back by itself, once each.

    make soak          one round
    make soak N=10     ten rounds; green ten times running is the bar

A round: each part killed outright (SIGKILL — a crash, not a request), the
brain killed outright, the keeper asked to go (SIGTERM). After each, the
brain must answer, every part be connected again by a process that began
after the kill, and no process be running twice. Run against the app on
this Mac: the voice falls silent for a few seconds each time.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live  # noqa: E402 — the live app's own helpers

BACK_S = 45.0
# The app starts a child that fell again at once only if it had lived this
# long; one that falls sooner waits longer each time, up to a minute
# (Child.swift). Falls every half minute are a crash loop, not life, so the
# app's own children are let live this long before they are killed again.
HEALTHY_S = 31.0


def pids(module: str) -> list[int]:
    found = subprocess.run(["pgrep", "-f", f"python3 -m {module}$"],
                           capture_output=True, text=True).stdout.split()
    return [int(p) for p in found]


def modules() -> dict[str, str]:
    """Each part the keeper runs: its channel → the module it runs."""
    found = {}
    for manifest in sorted((live.HOME / "parts").glob("*/heare.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            run = data["run"]
            found[data["channel"]] = run[run.index("-m") + 1]
        except (OSError, ValueError, KeyError, IndexError):
            continue
    return found


def bodies() -> dict[str, dict]:
    now = live.state() or {}
    return {b.get("channel"): b for b in (now.get("bodies") or {}).values()}


def back(after: float, parts: dict[str, str], fresh: list[str],
         session: str | None) -> str | None:
    """Until all is well again: every part connected, those in *fresh* by a
    process that began after the kill; what is still wrong, if it never is."""
    deadline = time.monotonic() + BACK_S
    wrong = "мозок не відповідає"
    while time.monotonic() < deadline:
        time.sleep(1.0)
        now = live.state()
        if now is None:
            wrong = "мозок не відповідає"
            continue
        if session is not None and now.get("session", {}).get("started") == session:
            wrong = "мозок той самий, що до вбивства"
            continue
        seen = {b.get("channel"): b for b in (now.get("bodies") or {}).values()}
        missing = [c for c in parts if seen.get(c, {}).get("state") != "approved"
                   or (c in fresh and (seen[c].get("started") or 0) < after)]
        twice = [m for m in ("heare.brain", "heare.keeper", *parts.values())
                 if len(pids(m)) > 1]
        if missing:
            wrong = "не повернулись: " + ", ".join(missing)
        elif twice:
            wrong = "двічі: " + ", ".join(twice)
        else:
            return None
    return wrong


def lived(pid: int) -> float:
    """Seconds since the process began; macOS's ps has only [[dd-]hh:]mm:ss."""
    out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    days, _, clock = out.rpartition("-")
    seconds = 0.0
    for piece in clock.split(":") if clock else []:
        seconds = seconds * 60 + float(piece)
    return seconds + float(days or 0) * 86400


def step(name: str, module: str, sig: int, parts: dict[str, str],
         fresh: list[str], changes_brain: bool = False) -> bool:
    victims = pids(module)
    if not victims:
        live.say(f"  {name}: не запущений")
        return False
    if module in ("heare.brain", "heare.keeper"):
        time.sleep(max(0.0, HEALTHY_S - min(lived(p) for p in victims)))
    session = (live.state() or {}).get("session", {}).get("started") if changes_brain else None
    after = time.time()
    started = time.monotonic()
    for pid in victims:
        os.kill(pid, sig)
    wrong = back(after, parts, fresh, session)
    took = time.monotonic() - started
    live.say(f"  {name}: {'✗ ' + wrong if wrong else 'повернулось'} за {took:.0f} с")
    return wrong is None


def round_(parts: dict[str, str]) -> bool:
    ok = True
    for channel, module in parts.items():
        # Only this part has to begin again; the others keep their process.
        ok &= step(channel, module, signal.SIGKILL, parts, [channel])
    # The parts outlive the brain: the same processes, connected again.
    ok &= step("мозок", "heare.brain", signal.SIGKILL, parts, [], changes_brain=True)
    ok &= step("наглядач", "heare.keeper", signal.SIGTERM, parts, list(parts))
    return ok


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    parts = modules()
    if live.state() is None:
        live.stop("heare не відповідає — спершу make live")
    green = 0
    for n in range(1, rounds + 1):
        live.say(f"коло {n}/{rounds}")
        if not round_(parts):
            live.say(f"червоне на колі {n}; зелених поспіль: {green}")
            return 1
        green += 1
    live.say(f"зелено {green} раз поспіль")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
