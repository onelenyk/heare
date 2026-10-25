"""Install, update and uninstall heare the way a person would — in a sandbox
beside the heare this Mac lives with.

    make e2e-dist

Two apps are built with their own identity (io.github.onelenyk.heare.e2e):
an "old" one at 0.0.1 from a copy of HEAD, and a "new" one from this tree.
They are installed into dist/e2e/Applications and run with HOME set to
dist/e2e/home, on ports 9890/9891, with keys kept in the process only. A
local server stands in for GitHub's latest release. Voice and hands stay
off: no microphone, no second Doka speaking in the room.

The path: install old → first run through the setup form → write to the
memory → the page's «Оновити» (through the door) → the new version runs,
the memory is there → uninstall keeping the memory → install new again,
the memory is picked up → uninstall everything. dist/e2e/report.txt says
what passed and how long each step took.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
E2E = REPO / "dist" / "e2e"
APPS = E2E / "Applications"
HOME = E2E / "home"
HEARE = HOME / ".heare"
FEED = E2E / "feed"
TRASH = E2E / "trash"
OLD_SRC = E2E / "old-src"
BUNDLE_ID = "io.github.onelenyk.heare.e2e"
OLD_VERSION = "0.0.1"
API, SETUP = 9890, 9891
APP = APPS / "heare.app"
BINARY = APP / "Contents/MacOS/heare"
MARK = "e2e: Назар любить чай"
WAIT_S = 120.0

report: list[str] = []


class Failed(Exception):
    pass


def say(text: str) -> None:
    print(text, flush=True)


def run(*argv: str | Path, cwd: Path | None = None, env: dict | None = None) -> str:
    done = subprocess.run([str(a) for a in argv], cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode:
        raise Failed(f"{' '.join(str(a) for a in argv[:3])}…: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def step(name: str):
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            say(f"\n== {name}")
            began = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                report.append(f"✗ {name} ({time.monotonic() - began:.0f} с): {exc}")
                raise
            report.append(f"✓ {name} ({time.monotonic() - began:.0f} с)")
            return result
        return inner
    return wrap


def wait(what: str, check, seconds: float = WAIT_S):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (OSError, ValueError, KeyError, Failed) as exc:
            last = exc
        time.sleep(1.0)
    raise Failed(f"не дочекався: {what} (останнє: {last!r})")


# -- the sandbox's doors ----------------------------------------------------

def token() -> str:
    return (HEARE / "machine" / "api_token").read_text(encoding="utf-8").strip()


def brain(path: str, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    request = urllib.request.Request(
        f"http://127.0.0.1:{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Heare-Token": token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.load(answer)


def form(path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{SETUP}{path}", method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as answer:
        return json.load(answer)


def apps_running() -> list[int]:
    found = subprocess.run(["pgrep", "-f", str(BINARY)], capture_output=True, text=True)
    return [int(p) for p in found.stdout.split()]


def children_running() -> list[int]:
    found = subprocess.run(["pgrep", "-f", f"{APP}/Contents/Resources/python"],
                           capture_output=True, text=True)
    return [int(p) for p in found.stdout.split()]


def env() -> dict[str, str]:
    return {"HOME": str(HOME), "HEARE_KEYS_BACKEND": "file", "HEARE_TRASH": str(TRASH)}


def launch() -> None:
    args = ["open", "-n", "-a", str(APP)]
    for name, value in env().items():
        args += ["--env", f"{name}={value}"]
    run(*args)
    wait("застосунок пісочниці", apps_running, 30)


def quit_app() -> None:
    for pid in apps_running():
        os.kill(pid, signal.SIGTERM)
    wait("застосунок вийшов", lambda: not apps_running() and not children_running(), 30)


def bodies() -> dict[str, dict]:
    return {b["channel"]: b for b in (brain("/api/state").get("bodies") or {}).values()}


# -- building -----------------------------------------------------------------

def build(src: Path, into: Path) -> None:
    run("sh", src / "apps/mac/build.sh", cwd=src, env={**os.environ, "HEARE_BUNDLE_ID": BUNDLE_ID})
    shutil.copy2(src / "dist" / "heare.dmg", into)


@step("збірка: стара 0.0.1 і нова")
def build_both() -> str:
    E2E.mkdir(parents=True, exist_ok=True)
    if OLD_SRC.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(OLD_SRC)], cwd=REPO)
    run("git", "worktree", "add", "--detach", OLD_SRC, "HEAD", cwd=REPO)
    for pyproject in [OLD_SRC / "pyproject.toml", *OLD_SRC.glob("packages/*/pyproject.toml")]:
        text = pyproject.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("version = ") and pyproject.parent.name != OLD_SRC.name:
                lines[i] = f'version = "{OLD_VERSION}"\n'
                break
        pyproject.write_text("".join(lines), encoding="utf-8")
    # The Python the app carries is the same file for both: fetch it once.
    cache = REPO / "apps/mac/.cache"
    cache.mkdir(parents=True, exist_ok=True)
    (OLD_SRC / "apps/mac/.cache").mkdir(parents=True, exist_ok=True)
    for tarball in cache.glob("cpython-*.tar.gz"):
        shutil.copy2(tarball, OLD_SRC / "apps/mac/.cache" / tarball.name)
    build(OLD_SRC, E2E / "old.dmg")
    build(REPO, E2E / "new.dmg")
    plist = run("plutil", "-extract", "CFBundleShortVersionString", "raw",
                REPO / "dist/heare.app/Contents/Info.plist").strip()
    return plist


# -- the feed: a local GitHub --------------------------------------------------

def serve_feed(new_version: str) -> http.server.ThreadingHTTPServer:
    FEED.mkdir(parents=True, exist_ok=True)
    name = f"heare-{platform.machine()}.dmg"
    shutil.copy2(E2E / "new.dmg", FEED / name)
    digest = hashlib.sha256((FEED / name).read_bytes()).hexdigest()
    (FEED / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", encoding="utf-8")
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:  # the report is the output
            pass

    handler = functools.partial(Quiet, directory=str(FEED))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    (FEED / "latest.json").write_text(json.dumps({
        "tag_name": f"v{new_version}", "body": "e2e: нова версія",
        "html_url": f"{base}/release", "assets": [
            {"name": name, "browser_download_url": f"{base}/{name}"},
            {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"}]}),
        encoding="utf-8")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# -- the path ----------------------------------------------------------------

def install(dmg: Path) -> None:
    mount = E2E / "mnt"
    mount.mkdir(parents=True, exist_ok=True)
    run("hdiutil", "attach", dmg, "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount, "-quiet")
    try:
        APPS.mkdir(parents=True, exist_ok=True)
        run("ditto", mount / "heare.app", APP)
    finally:
        run("hdiutil", "detach", mount, "-force", "-quiet")


@step("встановлення старої версії і перший запуск")
def first_run(feed: str) -> None:
    install(E2E / "old.dmg")
    settings = HEARE / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "config.toml").write_text(
        f'api_port = {API}\nsetup_port = {SETUP}\nupdate_feed = "{feed}"\n', encoding="utf-8")
    launch()
    machine = wait("форма першого запуску", lambda: form("/machine"))
    if machine["done"]:
        raise Failed("форма каже, що налаштування вже пройдено")
    form("/role", {"role": "brain"})
    for part in ("eyes", "sources"):
        form("/part", {"name": part, "on": True})
    form("/done", {})
    # A fresh Mac's parts wait for the person on the page: approve them as
    # the person would.
    waiting = wait("органи стукають", lambda: (b := bodies()) and all(
        c in b for c in ("eyes", "sources")) and b)
    for channel in ("eyes", "sources"):
        pid = next(k for k, v in brain("/api/state")["bodies"].items() if v["channel"] == channel)
        if waiting[channel]["state"] == "pending":
            brain(f"/api/bodies/{urllib.parse.quote(pid)}/approve", "POST", {})
    seen = wait("мозок і органи", lambda: (b := bodies()) and all(
        b.get(c, {}).get("state") == "approved" for c in ("eyes", "sources")) and b)
    if "voice" in seen or "opencode" in seen:
        raise Failed(f"у пісочниці зайві органи: {sorted(seen)}")
    version = brain("/api/state")["running"]["version"]
    if version != OLD_VERSION:
        raise Failed(f"мозок каже {version}, а не {OLD_VERSION}")
    if (HEARE / "layout").read_text().strip() != "2":
        raise Failed("~/.heare не розкладено")
    for section in ("memory", "settings", "machine"):
        if not (HEARE / section).is_dir():
            raise Failed(f"немає ~/.heare/{section}")


@step("памʼять: мітка в профілі")
def remember() -> None:
    brain("/api/mind/profile", "PUT", {"key": "user", "text": MARK})
    if MARK not in (HEARE / "memory/profile/user.md").read_text(encoding="utf-8"):
        raise Failed("мітка не лягла в профіль")


@step("оновлення кнопкою «Оновити»")
def update(new_version: str) -> None:
    checked = brain("/api/update/check", "POST", {})
    if checked.get("state") != "available" or checked.get("latest") != new_version:
        raise Failed(f"мозок не бачить нової версії: {checked}")
    if not checked.get("can_install"):
        raise Failed("мозок думає, що його запустив не застосунок")
    started = brain("/api/update/install", "POST", {}, timeout=120)
    if started.get("state") != "installing":
        raise Failed(f"оновлення не почалось: {started}")

    def new_brain():
        state = brain("/api/state")
        return state["running"]["version"] == new_version and state

    state = wait(f"мозок {new_version}", new_brain, 180)
    if state["update"].get("state") != "updated" or state["update"].get("previous") != OLD_VERSION:
        raise Failed(f"мозок не каже, що його оновлено: {state['update']}")
    wait("органи після оновлення", lambda: all(
        bodies().get(c, {}).get("state") == "approved" for c in ("eyes", "sources")))
    if len(apps_running()) != 1:
        raise Failed(f"застосунків {len(apps_running())}, а не один")
    wait("старий застосунок прибрано", lambda: not (APPS / "heare.app.old").exists(), 30)
    plist = run("plutil", "-extract", "CFBundleShortVersionString", "raw", APP / "Contents/Info.plist")
    if plist.strip() != new_version:
        raise Failed(f"у теці застосунок {plist.strip()}")
    if MARK not in (HEARE / "memory/profile/user.md").read_text(encoding="utf-8"):
        raise Failed("після оновлення мітки в профілі немає")
    if not list((HEARE / "backups").glob("heare-*.zip")):
        raise Failed("перед оновленням не зроблено копії")


def uninstall(how: str) -> None:
    quit_app()
    said = run(BINARY, "--uninstall", how, env={**os.environ, **env()})
    say("  " + said.strip().replace("\n", "\n  "))
    if APP.exists():
        raise Failed("застосунок лишився в Applications")
    if not (TRASH / "heare.app").exists() and not list(TRASH.glob("heare.app*")):
        raise Failed("застосунку немає і в Кошику пісочниці")


@step("видалення із памʼяттю")
def uninstall_keeping() -> None:
    uninstall("keep-memory")
    for kept in ("memory", "settings", "backups"):
        if not (HEARE / kept).is_dir():
            raise Failed(f"~/.heare/{kept} не лишився")
    if (HEARE / "machine").exists():
        raise Failed("~/.heare/machine лишився")
    if MARK not in (HEARE / "memory/profile/user.md").read_text(encoding="utf-8"):
        raise Failed("памʼять зникла")


@step("повторне встановлення підхоплює памʼять")
def reinstall(new_version: str) -> None:
    install(E2E / "new.dmg")
    launch()
    # The settings stayed: the form is done, the brain is up on its port.
    wait("мозок", lambda: brain("/api/state")["running"]["version"] == new_version)
    if not form("/machine")["done"]:
        raise Failed("налаштування питає вдруге")
    if MARK not in (HEARE / "memory/profile/user.md").read_text(encoding="utf-8"):
        raise Failed("памʼять не підхоплено")


@step("видалення всього")
def uninstall_all() -> None:
    uninstall("all")
    if HEARE.exists():
        raise Failed("~/.heare лишився")
    if not list(TRASH.glob(".heare*")):
        raise Failed("~/.heare немає і в Кошику пісочниці")


def clean() -> None:
    if apps_running():
        quit_app()
    for path in (APPS, HOME, FEED, TRASH, E2E / "mnt"):
        shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    if sys.platform != "darwin":
        say("e2e-dist — лише на маку")
        return 1
    clean()
    server = None
    try:
        new_version = build_both()
        server = serve_feed(new_version)
        feed = f"http://127.0.0.1:{server.server_address[1]}/latest.json"
        first_run(feed)
        remember()
        update(new_version)
        uninstall_keeping()
        reinstall(new_version)
        uninstall_all()
        ok = True
    except Failed as exc:
        say(f"\nспинився: {exc}")
        say(f"логи пісочниці: {HEARE / 'logs'}")
        ok = False
    finally:
        if server is not None:
            server.shutdown()
        if apps_running():
            quit_app()
    (E2E / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    say("\n" + "\n".join(report))
    say("зелено" if ok else "червоне")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
