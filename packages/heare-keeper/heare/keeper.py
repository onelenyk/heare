"""The keeper: one command for this machine's parts.

    uv run heare-keeper

Not a brain: it starts what the manifests under the parts
folder name, starts a part again when it falls, and stops it when the
manifest is taken away. The brain never holds a part's process; this one
does, and only this one.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from heare import layout, logs
from heare import lifeline, machine, part
from heare.report import Report
from heare.config import HOME, Settings
from heare.core.i18n import _
from heare.setup import serve
from heare.updating import Updating

logger = logging.getLogger("heare.keeper")

# How often the manifests and settings are looked at. The report every
# five seconds is the switch's wish; this only makes the wish land.
POLL_S = 2.0
# A part that falls is asked again after this, doubling to the ceiling: a
# broken part must not turn into a three-second loop that fills the log.
BACKOFF_S = 3.0
BACKOFF_MAX_S = 60.0
# A part that lived this long was not crashing at start; the next fall begins
# the backoff from the beginning again, as Child.swift does for the app.
HEALTHY_S = 30.0
# How long a part has to go when asked before it is killed. A part has
# nothing to save; two seconds is a courtesy, not a negotiation.
STOP_S = 2.0
LOCK_NAME = "keeper.lock"
# A part the person rejected, or the brain refused the key of, is not asked
# again by itself: a trust question every backoff is a yes clicked out of
# fatigue. It waits until the settings, a token or its manifest changes
# (any of which restarts every part) or its manifest is taken away and put
# back — all are new trust.
PARKED = (part.EXIT_REJECTED, part.EXIT_REFUSED)


def wanted(folder: Path) -> dict[str, list[str]]:
    """Each manifest's folder name -> the command it asks to run."""
    found: dict[str, list[str]] = {}
    for path in sorted(folder.glob("*/heare.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("not a manifest: %s", path)
            continue
        command = data.get("run") if isinstance(data, dict) else None
        if isinstance(command, list) and command:
            found[path.parent.name] = [str(part) for part in command]
    return found


# Modules renamed under a manifest already written (26 October: the hands
# became the cli hands); the manifest stays the person's, the name is ours.
RENAMED = {"heare.hands": "heare.cli_hands", "heare-hands": "heare-cli-hands"}


def _resolve(command: list[str]) -> list[str]:
    """`python` in a manifest means the interpreter the beacon runs under —
    the one with heare installed, not whatever is first on PATH."""
    command = [RENAMED.get(part, part) for part in command]
    if command[0].endswith("/heare-hands"):
        command = [command[0][: -len("heare-hands")] + "heare-cli-hands", *command[1:]]
    if command[0] in ("python", "python3"):
        return [sys.executable, *command[1:]]
    return command


class KeeperBusy(RuntimeError):
    """Another keeper already holds this machine's lock."""

    def __init__(self, holder: int | None) -> None:
        super().__init__(holder)
        self.holder = holder


_LOCKED: set[Path] = set()


def _holder(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


class Single:
    """One keeper per machine, held by the kernel, not by a stale pid file.

    ``flock`` is released when the process ends, however it ends, so a
    killed keeper leaves nothing that blocks the next one; the pid written
    in the file only says who holds it now, for the person reading the log.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None
        self._key: Path | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = self.path.resolve()
        if key in _LOCKED:
            raise KeeperBusy(_holder(self.path))
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as busy:
            holder = _holder(self.path)
            handle.close()
            raise KeeperBusy(holder) from busy
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle
        self._key = key
        _LOCKED.add(key)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
            if self._key is not None:
                _LOCKED.discard(self._key)


def _config_for(folder: Path) -> Path:
    return folder.parent.parent / "settings" / "config.toml"


def _stamp(path: Path) -> str:
    try:
        info = path.stat()
    except OSError:
        return "missing"
    return f"{info.st_mtime_ns}:{info.st_size}"


def _fingerprint(settings_path: Path, folder: Path) -> str:
    """The settings, keys and manifests that decide how a part knocks."""
    machine = settings_path.parent.parent / "machine"
    stamps = [_stamp(path) for path in (
        settings_path,
        machine / "machine_token",
        machine / "api_token",
    )]
    stamps.extend(_stamp(path) for path in sorted(folder.glob("*/heare.json")))
    try:
        stamps.append(hashlib.sha256(settings_path.read_bytes()).hexdigest())
    except OSError:
        pass
    return "|".join(stamps)


def _pairing(settings_path: Path) -> str:
    """Which trust this machine holds: a new token is a new pairing.

    Kept apart from the rest of the fingerprint: a setting changed while
    the machine is forgotten must not start its parts again with the old,
    refused key."""
    machine = settings_path.parent.parent / "machine"
    return "|".join(_stamp(path) for path in (
        machine / "machine_token", machine / "api_token"))


def _part_env() -> dict[str, str]:
    """A part watches the keeper, not the app: the keeper is its supervisor."""
    env = dict(os.environ)
    env["HEARE_PARENT_PID"] = str(os.getpid())
    return env


def _backoff(falls: int) -> float:
    return min(BACKOFF_S * (2 ** max(falls - 1, 0)), BACKOFF_MAX_S)


async def _park() -> None:
    """Hold a part's task until the keeper cancels it: a settings, token or
    manifest change, the only things that are a new trust."""
    await asyncio.Event().wait()


def _fall_why(code: int) -> str:
    reason = {
        part.EXIT_REJECTED: _("rejected"),
        part.EXIT_REFUSED: _("the brain refused the key"),
        part.EXIT_NO_ROUTE: _("the brain has no such route"),
        part.EXIT_BRAIN_ERROR: _("the brain answered with an error"),
    }.get(code)
    if reason:
        return _("fell (code {code}) — {why}").format(code=code, why=reason)
    return _("fell (code {code})").format(code=code)


class Keeper:
    def __init__(self, folder: Path, settings_path: Path | None = None) -> None:
        self._folder = folder
        self._settings = settings_path or _config_for(folder)
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._stopping = False
        self._lock = Single(folder.parent / LOCK_NAME)
        self._fails: dict[str, int] = {}
        self._restarts: dict[str, int] = {}
        # A part asked to start again by the person; the loop takes it down
        # and puts it back, and it is not counted as a fall.
        self._again: set[str] = set()
        self._fingerprint = _fingerprint(self._settings, folder)
        self._identity = _pairing(self._settings)
        # Why the machine's parts are not running at all — its brain has
        # forgotten the machine — or "" while it is trusted. Only a new
        # pairing lifts it: a setting is not a new yes.
        self._halted = ""

    def _note(self, name: str, state: str, why: str = "", **fields) -> None:
        try:
            machine.remember(self._folder, name, state, why,
                             restarts=self._restarts.get(name, 0), **fields)
        except OSError:
            logger.exception("could not write the state of %s", name)

    def halt(self, why: str) -> None:
        """The brain no longer knows this machine: stop its parts.

        The brain cannot knock on a forgotten machine, so the keeper is
        the one that learns — from the refused report — and stops what it
        raises. Every part says why, so the person sees it on the settings
        page instead of a machine that looks healthy and is not heard."""
        if self._halted:
            return
        self._halted = why
        for name in wanted(self._folder):
            self._note(name, "unwell", why)
        logger.warning("the machine is forgotten by the brain: %s", why)

    def restart(self, name: str) -> None:
        """The person asked this one to start again; the loop does it."""
        if name in machine.PARTS:
            self._again.add(name)

    async def _keep(self, name: str, command: list[str]) -> None:
        while not self._stopping:
            logger.info("%s: %s", name, " ".join(command))
            try:
                child = await asyncio.create_subprocess_exec(
                    *_resolve(command), start_new_session=True, env=_part_env())
            except OSError as exc:
                fails = self._fails.get(name, 0) + 1
                self._fails[name] = fails
                why = _("could not start {command}: {why}").format(
                    command=command[0], why=exc)
                logger.warning("%s: %s", name, why)
                self._note(name, "unwell", why, attempts=fails)
                await asyncio.sleep(_backoff(fails))
                continue
            self._running[name] = child
            group = _group_of(child)
            self._note(name, "running", "", pid=child.pid, attempts=0)
            started = time.monotonic()
            try:
                code = await child.wait()
            finally:
                self._running.pop(name, None)
                await _stop(child, group)
            if self._stopping:
                return
            why = _fall_why(code)
            if code in PARKED:
                logger.warning("%s: %s; keeping it stopped until the settings change", name, why)
                self._note(name, "unwell", why, attempts=0)
                await _park()
                return
            lived = time.monotonic() - started
            fails = 0 if lived >= HEALTHY_S else self._fails.get(name, 0) + 1
            self._fails[name] = fails
            self._restarts[name] = self._restarts.get(name, 0) + 1
            logger.warning("%s: %s; starting it again", name, why)
            self._note(name, "unwell", why, attempts=fails)
            await asyncio.sleep(_backoff(fails))

    async def _restart(self, tasks: dict[str, asyncio.Task]) -> None:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        tasks.clear()

    async def run(self) -> int:
        self._folder.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire()
        except KeeperBusy as busy:
            logs.say(_("another keeper already runs on this computer (pid {pid}) — this one stops").format(
                pid=busy.holder if busy.holder is not None else "?"))
            return 1
        tasks: dict[str, asyncio.Task] = {}
        try:
            while True:
                asked = wanted(self._folder)
                stamp = _fingerprint(self._settings, self._folder)
                pairing = _pairing(self._settings)
                if pairing != self._identity:
                    self._identity = pairing
                    if self._halted:
                        self._halted = ""
                        logs.say(_("this machine is paired again; its parts start"))
                if self._halted:
                    # A forgotten machine waits for a pairing, not for a
                    # setting: nothing here starts a part with its old key.
                    self._fingerprint = stamp
                    if tasks:
                        await self._restart(tasks)
                    for name in [known for known in machine.health(self._folder)
                                 if known not in asked]:
                        machine.forget(self._folder, name)
                    await asyncio.sleep(POLL_S)
                    continue
                if stamp != self._fingerprint:
                    self._fingerprint = stamp
                    logs.say(_("settings changed; the parts restart"))
                    await self._restart(tasks)
                    for name in [known for known in machine.health(self._folder)
                                 if known not in asked]:
                        machine.forget(self._folder, name)
                for name in [one for one in self._again if one in tasks]:
                    task = tasks.pop(name)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self._again.clear()
                for name, command in asked.items():
                    task = tasks.get(name)
                    if task is not None and task.done():
                        tasks.pop(name)
                    if name not in tasks:
                        tasks[name] = asyncio.create_task(self._keep(name, command))
                for name in [old for old in tasks if old not in asked]:
                    task = tasks.pop(name)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    machine.forget(self._folder, name)
                await asyncio.sleep(POLL_S)
        finally:
            self._stopping = True
            await self._restart(tasks)
            self._lock.release()
        return 0


def _group_of(child: asyncio.subprocess.Process) -> int | None:
    try:
        return os.getpgid(child.pid)
    except (ProcessLookupError, PermissionError):
        return None


def _signal_group(child: asyncio.subprocess.Process, sig: int, group: int | None) -> None:
    """Signal the part's whole session; its agents are in it too."""
    if group is None:
        group = _group_of(child)
    if group is None:
        return
    try:
        os.killpg(group, sig)
    except (ProcessLookupError, PermissionError):
        try:
            child.send_signal(sig)
        except ProcessLookupError:
            pass


def _group_alive(group: int | None) -> bool:
    if group is None:
        return False
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _stop(child: asyncio.subprocess.Process, group: int | None = None) -> None:
    """Ask a part and everything it started to go; make them go if not."""
    group = group if group is not None else _group_of(child)
    if child.returncode is None:
        _signal_group(child, signal.SIGTERM, group)
        try:
            await asyncio.wait_for(child.wait(), STOP_S)
        except TimeoutError:
            pass
    if _group_alive(group):
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if child.returncode is None:
        child.kill()
        await child.wait()


def _settings(config: Path, home: Path) -> Settings:
    """The token files live beside the settings, not in the developer's home:
    a test's temporary machine must not borrow a real key. The master token is
    ``machine/api_token`` — the brain's own keeper has no machine token, and
    before this it looked for a file that the installer never writes.
    """
    return Settings(
        path=config,
        api_token_file=home / "machine" / "api_token",
        machine_token_file=home / "machine" / "machine_token",
        machine_name_file=home / "machine" / "machine_name",
    )


async def kept(folder: Path, settings_path: Path | None = None) -> int:
    """Keep the parts until told to stop, and take them along when stopping.

    Without this, SIGTERM ended the keeper at once and no ``finally`` ran:
    the parts were never asked to go. The keeper also holds the machine's
    single lock; a second one says who has it and leaves without spawning.
    """
    config = Path(settings_path or _config_for(folder))
    home = config.parent.parent
    settings = _settings(config, home)
    keeper = Keeper(folder, settings_path)
    running = asyncio.create_task(keeper.run())
    # Held here: a task nobody keeps a reference to may be collected.
    taking = asyncio.create_task(Updating(settings, HOME).run())
    # What this machine has, and what the person asked it to run. The
    # keeper's own settings, not the developer's home: a test's temporary
    # machine must not report to a brain that happens to run here.
    reporting = asyncio.create_task(
        Report(settings, folder, on_restart=keeper.restart,
               on_refused=keeper.halt).run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, running.cancel)
    try:
        return await running
    except asyncio.CancelledError:
        return 0
    finally:
        taking.cancel()
        reporting.cancel()


def _serve_form() -> None:
    try:
        serve()
    except SystemExit as stop:
        logger.warning("%s", stop)
    except OSError as error:
        logger.warning("%s", error)
    except Exception:  # noqa: BLE001 — a UI failure must not stop supervision
        logger.exception("the setup form did not start")


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.keeper")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("command", nargs="?", choices=["setup"])
    args = parser.parse_args()
    logs.setup(args.verbose)
    layout.settle()
    settings = Settings()

    if args.command == "setup":
        try:
            serve()
        except OSError as error:
            logs.say(str(error))
            return 1
        return 0

    # The installer's page stays up after the first run: it is the settings.
    threading.Thread(target=_serve_form, name="heare-setup", daemon=True).start()

    lifeline.watch()
    return asyncio.run(kept(Path(settings.parts_dir), settings.path))


if __name__ == "__main__":
    sys.exit(main())
