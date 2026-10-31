"""Put this checkout live on this Mac as the app, or take the one before back.

    make live        check, build heare.app, replace the one in Applications,
                     start it, wait until every part is connected, say
    make rollback    the app before the live one, the same way
    make releases    what there is to go back to

Every release is kept as its whole app in ~/.heare/releases/<when>-<commit>/,
so going back runs exactly what ran before, not a rebuild of an old tree.
This is the developer's daily update; a person updates by downloading the
new .dmg.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".heare"
# This Mac's tokens and parts. Before 25 October they lay at the top; a
# release from then, put back by rollback, still keeps them there.
MACHINE = HOME / "machine" if (HOME / "machine").is_dir() else HOME
RELEASES = HOME / "releases"
LIVE = RELEASES / "live"
INSTALLED = RELEASES / "installed"
HEALTHY = "healthy"
KEEP = 5
BASE = os.environ.get("HEARE_URL", "http://127.0.0.1:9790")
WAIT_S = 90.0
STOP_S = 20.0
REPO = Path(__file__).resolve().parents[1]
APP = Path("/Applications/heare.app")
BINARY = APP / "Contents/MacOS/heare"
# Commands a terminal still wants, run with the app's own Python.
SHIMS = {"heare-chat": "heare.chat"}
SHIM_DIR = Path.home() / ".local/bin"


def say(text: str = "") -> None:
    print(text, flush=True)


def stop(text: str) -> None:
    print(f"спинився: {text}", file=sys.stderr)
    raise SystemExit(1)


def run(*argv: str, cwd: Path | None = None) -> str:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        stop(f"{' '.join(argv[:3])}…\n{done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


# -- releases ---------------------------------------------------------------

def releases() -> list[Path]:
    """Oldest first; a release is a folder with its app and a note."""
    if not RELEASES.is_dir():
        return []
    return sorted(p for p in RELEASES.iterdir()
                  if (p / "release.json").exists() and (p / "heare.app").is_dir())


def live_name() -> str:
    try:
        return LIVE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def installed_name() -> str:
    try:
        return INSTALLED.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def is_healthy(release: Path) -> bool:
    """Whether this release was seen running with every part connected.

    Releases from before the healthy note have none; one at or before the
    live marker did run, and one newer than it never became live.
    """
    if (release / HEALTHY).exists():
        return True
    live = live_name()
    return bool(live) and release.name <= live


def mark_healthy(release: Path) -> None:
    (release / HEALTHY).write_text(
        datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def note(release: Path) -> dict:
    return json.loads((release / "release.json").read_text(encoding="utf-8"))


def build() -> Path:
    commit = run("git", "rev-parse", "--short", "HEAD", cwd=REPO).strip()
    dirty = bool(run("git", "status", "--porcelain", cwd=REPO).strip())
    if dirty:
        say("  ! у дереві є незакомічене — воно теж поїде, але відкат до нього"
            " буде без коміту")
    say("  збираю застосунок (make app)…")
    run("make", "app", cwd=REPO)
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{commit}" + ("-dirty" if dirty else "")
    release = RELEASES / name
    release.mkdir(parents=True)
    # ditto keeps the bundle as macOS made it: links, modes, the signature.
    run("ditto", str(REPO / "dist/heare.app"), str(release / "heare.app"))
    (release / "release.json").write_text(json.dumps({
        "commit": commit, "dirty": dirty,
        "built": datetime.now().isoformat(timespec="seconds"),
        "subject": run("git", "log", "-1", "--format=%s", cwd=REPO).strip(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return release


def prune() -> None:
    """Keep the newest few, and never the live one. Releases from before the
    app (wheels only) cannot run any more and go too."""
    if not RELEASES.is_dir():
        return
    runnable = releases()
    for old in RELEASES.iterdir():
        if old.is_dir() and old not in runnable and old.name != live_name():
            shutil.rmtree(old, ignore_errors=True)
    for old in runnable[:-KEEP]:
        if old.name != live_name():
            shutil.rmtree(old, ignore_errors=True)


# -- the app ----------------------------------------------------------------

def running_app() -> list[int]:
    found = subprocess.run(["pgrep", "-f", str(BINARY)], capture_output=True, text=True)
    return [int(pid) for pid in found.stdout.split()]


def quit_app() -> None:
    """Ask the app to go — it stops the brain, the keeper and the parts —
    and wait until it has."""
    pids = running_app()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + STOP_S
    while running_app() and time.monotonic() < deadline:
        time.sleep(0.3)
    for pid in running_app():
        os.kill(pid, signal.SIGKILL)
    if pids:
        say("  зупинив застосунок")


def install(release: Path) -> None:
    quit_app()
    staged = APP.with_name("heare.app.new")
    aside = APP.with_name("heare.app.old")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(aside, ignore_errors=True)
    run("ditto", str(release / "heare.app"), str(staged))
    had = APP.exists()
    if had:
        APP.rename(aside)
    try:
        staged.rename(APP)
    except OSError:
        if had and aside.exists():
            aside.rename(APP)
        raise
    if had:
        shutil.rmtree(aside, ignore_errors=True)
    INSTALLED.write_text(release.name, encoding="utf-8")
    python = APP / "Contents/Resources/python/bin/python3"
    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    for name, module in SHIMS.items():
        shim = SHIM_DIR / name
        if shim.is_symlink() or shim.exists():
            shim.unlink()
        shim.write_text(f'#!/bin/sh\nexec "{python}" -m {module} "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    say(f"  поставив {APP}")


def start() -> None:
    run("open", str(APP))


def state() -> dict | None:
    try:
        token = (MACHINE / "api_token").read_text(encoding="utf-8").strip()
        ask = urllib.request.Request(f"{BASE}/api/state",
                                     headers={"X-Heare-Token": token})
        with urllib.request.urlopen(ask, timeout=2) as answer:
            return json.load(answer)
    except (OSError, ValueError):
        return None


def wanted_parts() -> dict[str, str]:
    """The parts the keeper runs here: their channel, by folder name."""
    found = {}
    for manifest in sorted((MACHINE / "parts").glob("*/heare.json")):
        try:
            found[manifest.parent.name] = json.loads(manifest.read_text(encoding="utf-8"))["channel"]
        except (OSError, ValueError, KeyError):
            continue
    return found


def wait(before: str) -> bool:
    """Until the brain answers as a new one and every part is connected."""
    wanted = wanted_parts()
    deadline = time.monotonic() + WAIT_S
    seen: dict = {}
    while time.monotonic() < deadline:
        now = state()
        if now and now.get("session", {}).get("started") != before:
            seen = {b.get("channel"): b.get("state") for b in now.get("bodies", {}).values()
                    if not str(b.get("channel")).startswith("web")}
            if all(seen.get(channel) == "approved" for channel in wanted.values()):
                return True
        time.sleep(1.0)
    say("  не дочекався:")
    for name, channel in wanted.items():
        say(f"    {name}: {seen.get(channel) or 'не стукав'}")
    return False


def falls_since(offsets: dict[Path, int]) -> list[str]:
    found = []
    for log, offset in offsets.items():
        try:
            with log.open(encoding="utf-8", errors="replace") as lines:
                lines.seek(offset)
                found += [line.strip() for line in lines if " fell " in line]
        except OSError:
            continue
    return found


def put_live(release: Path, why: str) -> int:
    started = time.monotonic()
    was = state()
    before = (was or {}).get("session", {}).get("started", "")
    logs = [HOME / "logs" / "app-keeper.log"]
    offsets = {log: log.stat().st_size if log.exists() else 0 for log in logs}
    say(f"{why}: {release.name}")
    install(release)
    start()
    ok = wait(before)
    falls = falls_since(offsets)
    if not ok or falls:
        for line in falls[-5:]:
            say(f"  впало: {line}")
        say(f"не вийшло за {time.monotonic() - started:.0f} с — журнали:"
            f" {HOME / 'logs'}; назад: make rollback")
        return 1
    mark_healthy(release)
    LIVE.write_text(release.name, encoding="utf-8")
    n = note(release)
    parts = ", ".join(sorted(wanted_parts())) or "без частин"
    say(f"живе {n['commit']}{' (з незакоміченим)' if n['dirty'] else ''} — "
        f"«{n.get('subject', '')}»")
    say(f"за {time.monotonic() - started:.0f} с; мозок і {parts} під'єднані, падінь 0")
    return 0


# -- the commands -----------------------------------------------------------

def live() -> int:
    if sys.platform != "darwin":
        stop("лише Mac; на сервері — install.sh")
    RELEASES.mkdir(parents=True, exist_ok=True)
    release = build()
    code = put_live(release, "оновлюю")
    prune()
    return code


def rollback(to: str = "") -> int:
    known = releases()
    if to:
        # By its name, or by its commit: TO=8ed418f is enough.
        def commit(release: Path) -> str:
            parts = release.name.split("-")
            return parts[2] if len(parts) > 2 else ""

        target = next((r for r in reversed(known)
                       if r.name.startswith(to) or commit(r).startswith(to)), None)
        if target is None:
            stop(f"немає релізу {to} — дивись: make releases")
    else:
        current = live_name()
        installed = installed_name()
        # A failed live installs a release that never became healthy. The
        # marker still names the last good one, so go back to that release —
        # not to the one before it, which is what a plain "step back" does.
        newer = [r for r in known if current and r.name > current]
        if newer or (installed and installed != current):
            candidates = [r for r in known
                          if is_healthy(r) and (r.name != installed or r.name == current)]
        elif current:
            candidates = [r for r in known if is_healthy(r) and r.name < current]
        else:
            candidates = [r for r in known if is_healthy(r)]
        if not candidates:
            stop("нема на що повертатись — дивись: make releases")
        target = candidates[-1]
    return put_live(target, "повертаю")


def listing() -> int:
    current = live_name()
    known = releases()
    if not known:
        say("релізів ще немає — make live")
    for release in reversed(known):
        n = note(release)
        mark = "●" if release.name == current else " "
        say(f"{mark} {release.name}  {n.get('subject', '')}")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "live"
    if command == "live":
        return live()
    if command == "rollback":
        return rollback(sys.argv[2] if len(sys.argv) > 2 else "")
    if command == "releases":
        return listing()
    stop(f"не знаю «{command}»: live, rollback [реліз], releases")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
