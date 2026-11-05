"""End to end: every phone-hands skill, through the real brain, on the real phone.

Each skill is called the way the brain's hands call it (POST /api/skills/call
on the brain), and what happened is checked on the phone by adb, which does
not go through heare at all. Needs: the phone on USB, heare's accessibility
service on, and a brain: the live one on the mini-PC over ssh, or a test
brain on this computer (HEARE_E2E_HOME: its home; the port from its config).

    python3 apps/mobile/e2e/hands.py                       # the live brain
    HEARE_E2E_HOME=... ANDROID_SERIAL=emulator-5554 python3 apps/mobile/e2e/hands.py
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
import shlex
import subprocess
import sys
import time

BRAIN = "nazar@192.168.110.13"
PORT = 8095
LOCAL = Path(os.environ["HEARE_E2E_HOME"]) if os.environ.get("HEARE_E2E_HOME") else None
SETTINGS = "com.android.settings"

failed: list[str] = []


def adb(*args: str) -> str:
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=30).stdout


def phone_name() -> str:
    prefs = adb("shell", "run-as", "dev.onelenyk.heare", "cat", "shared_prefs/heare.xml")
    start = prefs.index('name="name">') + len('name="name">')
    return prefs[start:prefs.index("<", start)]


PART = f"phone-hands@{phone_name()}"


def brain(method: str, path: str, body: dict | None = None) -> object:
    if LOCAL:
        port = re.search(r"api_port\s*=\s*(\d+)", (LOCAL / ".heare/settings/config.toml").read_text())
        request = urllib.request.Request(
            f"http://127.0.0.1:{port.group(1) if port else 9790}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Heare-Token": (LOCAL / ".heare/machine/api_token").read_text().strip(),
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=40) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as refused:
            return {"ok": False, "status": refused.code, "result": refused.read().decode()}
    data = f"-d {shlex.quote(json.dumps(body))}" if body is not None else ""
    remote = (f'curl -s -X {method} -H "X-Heare-Token: $(cat ~/.heare/machine/api_token)" '
              f'-H "Content-Type: application/json" {data} http://127.0.0.1:{PORT}{path}')
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", BRAIN, remote],
                         capture_output=True, text=True, timeout=40).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "result": out}


def call(skill: str, **args: object) -> dict:
    answer = brain("POST", "/api/skills/call", {"part": PART, "name": skill, "args": args})
    assert isinstance(answer, dict)
    return answer


def front() -> str:
    """The app in front, as Android itself says, not as heare does."""
    for line in adb("shell", "dumpsys", "window").splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            return line
    return ""


def check(what: str, ok: bool, seen: object = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + what + ("" if ok else f"  — {seen}"))
    if not ok:
        failed.append(what)


def main() -> int:
    print(f"phone-hands e2e: {PART}")

    skills = brain("GET", "/api/skills")
    mine = skills.get(PART) if isinstance(skills, dict) else None
    names = sorted(s["name"] for s in (mine or {}).get("skills", []))
    want = sorted(["screen", "active_app", "apps", "back", "home", "scroll", "open_app", "tap", "type"])
    check("the brain took all nine skills", names == want, names or skills)

    off = call("active_app")
    if off.get("ok") is False and "accessibility" in str(off.get("result")):
        check("accessibility is on (turn heare on in Accessibility)", False, off)
        return 1

    # From a known place: Settings as if just started (adb, not heare).
    adb("shell", "am", "force-stop", SETTINGS)
    adb("shell", "am", "force-stop", "com.google.android.settings.intelligence")

    a = call("home")
    check("home: ok", a.get("ok") is True, a)
    check("home: the launcher is in front", "launcher" in front().lower(), front())

    a = call("apps")
    check("apps: Settings is among them", a.get("ok") is True and SETTINGS in str(a.get("result")), a)

    a = call("open_app", name="Settings")
    check("open_app Settings: ok", a.get("ok") is True, a)
    check("open_app Settings: Android shows Settings", SETTINGS in front(), front())

    a = call("active_app")
    check("active_app: says Settings", a.get("result") == SETTINGS, a)

    a = call("screen")
    shown = (a.get("result") or {}) if isinstance(a.get("result"), dict) else {}
    check("screen: the app is Settings", shown.get("app") == SETTINGS, a)
    check("screen: numbered elements, some to tap", "[tap" in str(shown.get("elements")), a)

    a = call("scroll", direction="down")
    check("scroll down: ok", a.get("ok") is True, a)
    a = call("scroll", direction="up")
    check("scroll up: ok", a.get("ok") is True, a)

    a = call("tap", text="Search settings")
    check("tap by text (Search settings): ok", a.get("ok") is True, a)
    a = call("type", text="battery")
    check("type: ok", a.get("ok") is True, a)
    a = call("screen")
    check("type: the field holds what was typed", "battery" in str(a.get("result")).lower(), a)

    call("back")
    call("back")
    a = call("open_app", name="Settings")
    check("open_app again: Settings opens where it was left", a.get("ok") is True, a)
    a = call("screen")
    elements = str((a.get("result") or {}).get("elements", "")) if isinstance(a.get("result"), dict) else ""
    # A row is tapped, its words are inside it: the last [tap] before «Network».
    lines = elements.splitlines()
    named = next((i for i, line in enumerate(lines) if "Network" in line), None)
    row = next((lines[i] for i in range(named, -1, -1) if "[tap" in lines[i]), None) if named else None
    if row is None:
        check("tap by index: something to tap", False, elements[:300])
    else:
        index = int(row.split(" ", 1)[0])
        before = elements
        a = call("tap", index=index)
        after = call("screen")
        check(f"tap by index ({row.strip()[:50]}): ok", a.get("ok") is True, a)
        check("tap by index: the screen changed", str(after.get("result")) != before, after)

    a = call("back")
    check("back: ok", a.get("ok") is True, a)

    a = call("tap", index=99999)
    check("tap a number not on screen: refused", a.get("ok") is False, a)
    a = call("open_app", name="no-such-app-here")
    check("open_app unknown: refused", a.get("ok") is False, a)
    a = call("no_such_skill")
    check("an unknown skill: the brain refuses", a.get("ok") is not True, a)

    call("home")
    print(f"\n{'all passed' if not failed else f'{len(failed)} failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
