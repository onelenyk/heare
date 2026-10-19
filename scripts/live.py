"""Put this checkout live on this Mac, or take the one before back.

    make live        build, install what is installed, restart, wait, say
    make rollback    the release before the live one, the same way
    make releases    what there is to go back to

Every release is kept as its wheels in ~/.heare/releases/<when>-<commit>/,
so going back installs exactly what ran before, not a rebuild of an old
tree. Only the tools already installed are updated: this is the daily
update, not the installer (install.sh writes manifests and services).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".heare"
RELEASES = HOME / "releases"
LIVE = RELEASES / "live"
KEEP = 5
BASE = os.environ.get("HEARE_URL", "http://127.0.0.1:9790")
WAIT_S = 60.0
REPO = Path(__file__).resolve().parents[1]
UID = os.getuid()

# What each tool is installed with, besides its own wheel.
TOOLS = {
    "heare-brain": ("core", "reach"),
    "heare-hands": ("core", "reach"),
    "heare-eyes": ("core",),
    "heare-voice": ("core",),
    "heare-chat": ("core",),
    "heare-keeper": ("core",),
}
# The parts the keeper runs; one that outlived its keeper is an orphan.
PARTS = re.compile(r"/bin/heare-(eyes|voice|hands)$")


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
    """Oldest first; a release is a folder with its wheels and a note."""
    if not RELEASES.is_dir():
        return []
    return sorted(p for p in RELEASES.iterdir() if (p / "release.json").exists())


def live_name() -> str:
    try:
        return LIVE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def note(release: Path) -> dict:
    return json.loads((release / "release.json").read_text(encoding="utf-8"))


def build() -> Path:
    commit = run("git", "rev-parse", "--short", "HEAD", cwd=REPO).strip()
    dirty = bool(run("git", "status", "--porcelain", cwd=REPO).strip())
    if dirty:
        say("  ! у дереві є незакомічене — воно теж поїде, але відкат до нього"
            " буде без коміту")
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{commit}" + ("-dirty" if dirty else "")
    release = RELEASES / name
    release.mkdir(parents=True)
    run("uv", "build", "--all-packages", "-q", "-o", str(release), cwd=REPO)
    (release / "release.json").write_text(json.dumps({
        "commit": commit, "dirty": dirty,
        "built": datetime.now().isoformat(timespec="seconds"),
        "subject": run("git", "log", "-1", "--format=%s", cwd=REPO).strip(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return release


def prune() -> None:
    """Keep the newest few, and never the live one."""
    kept = releases()
    for old in kept[:-KEEP]:
        if old.name != live_name():
            shutil.rmtree(old, ignore_errors=True)


# -- installing and restarting ----------------------------------------------

def installed() -> list[str]:
    listed = run("uv", "tool", "list")
    return [tool for tool in TOOLS if re.search(rf"^{tool} ", listed, re.M)]


def install(release: Path) -> None:
    def wheel(name: str) -> str:
        found = sorted(release.glob(f"heare_{name}-*.whl"))
        if not found:
            stop(f"у {release.name} немає heare-{name}")
        return str(found[-1])

    for tool in installed():
        withs = [arg for dep in TOOLS[tool] for arg in ("--with", wheel(dep))]
        # --reinstall: every release is version 0.1.0, and uv must not keep
        # what it has because the number did not change.
        run("uv", "tool", "install", "-q", "--force", "--reinstall",
            wheel(tool.removeprefix("heare-")), *withs)
        say(f"  поставив {tool}")


def state() -> dict | None:
    try:
        token = (HOME / "api_token").read_text(encoding="utf-8").strip()
        ask = urllib.request.Request(f"{BASE}/api/state",
                                     headers={"X-Heare-Token": token})
        with urllib.request.urlopen(ask, timeout=2) as answer:
            return json.load(answer)
    except (OSError, ValueError):
        return None


def orphans() -> list[int]:
    """Parts whose keeper is gone — one run by hand and stopped — that the
    new keeper would otherwise run a second time."""
    listed = run("ps", "-axo", "pid=,ppid=,command=")
    found = []
    for line in listed.splitlines():
        pid, ppid, command = line.split(None, 2)
        if ppid == "1" and PARTS.search(command):
            found.append(int(pid))
    return found


def restart() -> None:
    # The keeper first: its parts stop with it, and the new ones wait for
    # the new brain instead of reconnecting to the old one.
    for label in ("com.heare.keeper", "com.heare.brain"):
        run("launchctl", "kickstart", "-k", f"gui/{UID}/{label}")
    time.sleep(1.0)
    for pid in orphans():
        os.kill(pid, signal.SIGTERM)
        say(f"  прибрав сироту {pid}")


def wanted_parts() -> dict[str, str]:
    """The parts the keeper runs here: their channel, by folder name."""
    found = {}
    for manifest in sorted((HOME / "parts").glob("*/heare.json")):
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


def falls_since(offset: int) -> list[str]:
    log = HOME / "logs" / "parts.log"
    try:
        with log.open(encoding="utf-8", errors="replace") as lines:
            lines.seek(offset)
            return [line.strip() for line in lines if " fell " in line]
    except OSError:
        return []


def put_live(release: Path, why: str) -> int:
    started = time.monotonic()
    was = state()
    before = (was or {}).get("session", {}).get("started", "")
    log = HOME / "logs" / "parts.log"
    offset = log.stat().st_size if log.exists() else 0
    say(f"{why}: {release.name}")
    install(release)
    restart()
    ok = wait(before)
    falls = falls_since(offset)
    if not ok or falls:
        for line in falls[-5:]:
            say(f"  впало: {line}")
        say(f"не вийшло за {time.monotonic() - started:.0f} с — журнали:"
            f" {HOME / 'logs'}; назад: make rollback")
        return 1
    LIVE.write_text(release.name, encoding="utf-8")
    n = note(release)
    parts = ", ".join(sorted(wanted_parts()))
    say(f"живе {n['commit']}{' (з незакоміченим)' if n['dirty'] else ''} — "
        f"«{n.get('subject', '')}»")
    say(f"за {time.monotonic() - started:.0f} с; мозок і {parts} під'єднані, падінь 0")
    return 0


# -- the commands -----------------------------------------------------------

def live() -> int:
    if sys.platform != "darwin":
        stop("лише Mac (launchd); на сервері — install.sh")
    RELEASES.mkdir(parents=True, exist_ok=True)
    say("збираю…")
    release = build()
    code = put_live(release, "оновлюю")
    prune()
    return code


def rollback(to: str = "") -> int:
    known = releases()
    if to:
        target = next((r for r in known if r.name == to or r.name.startswith(to)), None)
        if target is None:
            stop(f"немає релізу {to} — дивись: make releases")
    else:
        names = [r.name for r in known]
        current = live_name()
        older = known[:names.index(current)] if current in names else known[:-1]
        if not older:
            stop("нема на що повертатись — дивись: make releases")
        target = older[-1]
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
