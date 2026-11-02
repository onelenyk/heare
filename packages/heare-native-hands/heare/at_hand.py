"""What the native hands do on their own machine: files and plain commands.

Reading is free and writing is the person's: a command on the list below
runs at once, anything else is asked in its whole words, never of the judge.
A command is split and run without a shell, so ``;``, ``|`` and ``>`` are
only characters. The list is the boundary: ask to add to it, do not widen a
rule in passing. Secrets stay out of reach of the free path, not of a yes.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path

from heare import hand as hand_life

LIST, READ, RUN, WRITE = "list_files", "read_file", "run_command", "write_file"
NAMES = frozenset({LIST, READ, RUN, WRITE})
OUTPUT_CHARS = 8000
RUN_S = 30.0

FREE = frozenset({
    "ls", "pwd", "date", "cal", "whoami", "uname", "uptime", "sw_vers", "df",
    "du", "ps", "which", "wc", "head", "tail", "cat", "grep", "find", "file",
    "stat", "git",
})
FREE_GIT = frozenset({"status", "log", "diff", "show", "branch"})
FIND_WRITES = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir",
                         "-fprint", "-fprintf", "-fls"})
SECRET = (".ssh", ".gnupg", ".aws", ".netrc", ".env", "Keychains",
          ".heare/machine", ".config/gcloud", "id_rsa", "id_ed25519")


def secret(text: str) -> bool:
    return any(mark in text for mark in SECRET)


def is_free(argv: list[str]) -> bool:
    """Whether a command only looks; when in doubt it is not."""
    if not argv or argv[0] not in FREE or any(secret(arg) for arg in argv):
        return False
    name, rest = argv[0], argv[1:]
    if name == "git":
        return bool(rest) and rest[0] in FREE_GIT and "--output" not in " ".join(rest)
    if name == "find":
        return not FIND_WRITES & set(rest)
    if name == "grep":
        # A whole tree would walk past the secret marks unseen.
        return not any(a.startswith("-") and ("r" in a.lower()) for a in rest)
    if name == "tail":
        return not any(a.startswith("-") and "f" in a.lower() for a in rest)
    return True


def _capped(text: str) -> str:
    if len(text) <= OUTPUT_CHARS:
        return text or "(nothing)"
    return text[:OUTPUT_CHARS] + f"\n… cut, {len(text)} characters in all"


def _exec(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=RUN_S, cwd=Path.home(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"Failed: no end in {int(RUN_S)} s; whether it happened is unknown."
    except OSError as exc:
        return f"Failed: {exc}"
    said = _capped(done.stdout + done.stderr)
    return said if done.returncode == 0 else f"Failed ({done.returncode}): {said}"


def _path(raw: object) -> Path:
    return Path(str(raw or "~")).expanduser()


async def _list(args: dict) -> str:
    path = _path(args.get("path"))
    if secret(str(path)):
        return "Not done: this folder holds secrets."
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
    except OSError as exc:
        return f"Failed: {exc}"
    return _capped("\n".join(names))


async def _read(args: dict) -> str:
    path = _path(args.get("path"))
    if secret(str(path.resolve())):
        return "Not done: this file holds secrets."
    try:
        return _capped(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return f"Failed: {exc}"


async def _write(args: dict, desk: hand_life.Desk) -> str:
    path, text = _path(args.get("path")), str(args.get("text") or "")
    what = f"write {len(text)} characters to {path}"
    if not await desk.permit(what, "change", f"{what}\n{text[:2000]}"):
        return "Not done: no permission."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"Failed: {exc}"
    return f"Written: {path}"


async def _run(args: dict, desk: hand_life.Desk) -> str:
    command = str(args.get("command") or "").strip()
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"Failed: {exc}"
    if not argv:
        return "Failed: no command."
    if not is_free(argv) and not await desk.permit(command, "change", command):
        return "Not done: no permission."
    return await asyncio.to_thread(_exec, argv)


async def run(name: str, args: dict, desk: hand_life.Desk) -> str:
    desk.step(name)
    if name == LIST:
        return await _list(args)
    if name == READ:
        return await _read(args)
    if name == WRITE:
        return await _write(args, desk)
    return await _run(args, desk)


def _tool(name: str, about: str, **fields: str) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": about,
        "parameters": {"type": "object", "required": list(fields), "properties": {
            key: {"type": "string", "description": words}
            for key, words in fields.items()}}}}


SCHEMAS = [
    _tool(LIST, "List a folder on this machine.", path="The folder; ~ is allowed."),
    _tool(READ, "Read a text file on this machine.", path="The file; ~ is allowed."),
    _tool(WRITE, "Write a text file on this machine; the person is asked first.",
          path="The file; ~ is allowed.", text="The whole new content."),
    _tool(RUN, "Run one command on this machine, with no shell: no pipes, no "
               "redirects. Looking commands (ls, cat, grep, find, df, ps, git "
               "status…) run at once; any other is asked of the person first.",
          command="The command line."),
]
