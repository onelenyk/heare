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
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from heare.config import Settings
from heare.setup import serve, want_setup

logger = logging.getLogger("heare.keeper")

POLL_S = 5.0
BACKOFF_S = 3.0
# How long a part has to go when asked before it is killed.
STOP_S = 5.0


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


def _resolve(command: list[str]) -> list[str]:
    """`python` in a manifest means the interpreter the beacon runs under —
    the one with heare installed, not whatever is first on PATH."""
    if command[0] in ("python", "python3"):
        return [sys.executable, *command[1:]]
    return command


class Keeper:
    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._stopping = False

    async def _keep(self, name: str, command: list[str]) -> None:
        while not self._stopping:
            logger.info("%s: %s", name, " ".join(command))
            child = await asyncio.create_subprocess_exec(*_resolve(command))
            self._running[name] = child
            try:
                code = await child.wait()
            finally:
                self._running.pop(name, None)
                if child.returncode is None:
                    await _stop(child)
            if self._stopping:
                return
            logger.warning("%s fell (code %s); starting it again", name, code)
            await asyncio.sleep(BACKOFF_S)

    async def run(self) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        tasks: dict[str, asyncio.Task] = {}
        try:
            while True:
                asked = wanted(self._folder)
                for name, command in asked.items():
                    if name not in tasks:
                        tasks[name] = asyncio.create_task(self._keep(name, command))
                for name in [old for old in tasks if old not in asked]:
                    tasks.pop(name).cancel()
                await asyncio.sleep(POLL_S)
        finally:
            self._stopping = True
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)


async def _stop(child: asyncio.subprocess.Process) -> None:
    """Ask a part to go, and make it go if it does not."""
    child.terminate()
    try:
        await asyncio.wait_for(child.wait(), STOP_S)
    except TimeoutError:
        child.kill()
        await child.wait()


async def kept(folder: Path) -> None:
    """Keep the parts until told to stop, and take them along when stopping.

    Without this, SIGTERM ended the keeper at once and no ``finally`` ran:
    the parts were never asked to go. launchd killed them with the keeper's
    process group; a keeper run by hand left them running, and the next one
    ran each of them a second time."""
    running = asyncio.create_task(Keeper(folder).run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, running.cancel)
    try:
        await running
    except asyncio.CancelledError:
        pass


def _serve_form() -> None:
    try:
        serve()
    except SystemExit as stop:
        logger.warning("%s", stop)
    except Exception:  # noqa: BLE001 — a UI failure must not stop supervision
        logger.exception("the setup form did not start")


def main() -> int:
    parser = argparse.ArgumentParser(prog="heare.keeper")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("command", nargs="?", choices=["setup"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    settings = Settings()

    if args.command == "setup":
        serve()
        return 0

    if want_setup(settings):
        threading.Thread(
            target=_serve_form, name="heare-setup", daemon=True,
        ).start()

    asyncio.run(kept(Path(settings.parts_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
