"""What the native hands do on their own machine: files and plain commands.

Reading is free and writing is the person's: a command on the list below
runs at once, anything else is asked in its whole words, never of the judge.
A command is split and run without a shell, so ``;``, ``|`` and ``>`` are
only characters. The list is the boundary: ask to add to it, do not widen a
rule in passing. Secrets stay out of reach of the free path, not of a yes.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shlex
import subprocess
from pathlib import Path

from heare import fence
from heare import hand as hand_life

LIST, READ, RUN, WRITE = "list_files", "read_file", "run_command", "write_file"
EDIT, SEARCH, PLAN, RECIPE = "edit_file", "search_text", "keep_plan", "keep_recipe"
NAMES = frozenset({LIST, READ, RUN, WRITE, EDIT, SEARCH, PLAN, RECIPE})
OUTPUT_CHARS = 8000
RUN_S = 30.0
DEEP_RUN_S = 300.0
WORK = "heare-work"

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


def _exec(argv: list[str], limit: float = RUN_S, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=limit, cwd=cwd or Path.home(),
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"Failed: no end in {int(limit)} s; whether it happened is unknown."
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
    work = (Path.home() / WORK).resolve()
    mine = path.resolve().is_relative_to(work)
    if not mine and not await desk.permit(what, "change", f"{what}\n{text[:2000]}"):
        return "Not done: no permission."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"Failed: {exc}"
    return f"Written: {path}"


async def _edit(args: dict, desk: hand_life.Desk) -> str:
    """One exact piece replaced: it must be there, and there once."""
    path, old = _path(args.get("path")), str(args.get("old") or "")
    new = str(args.get("new") or "")
    if secret(str(path.resolve())):
        return "Not done: this file holds secrets."
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Failed: {exc}"
    if not old or text.count(old) != 1:
        return f"Failed: the piece is there {text.count(old) if old else 0} times, not once."
    return await _write({"path": str(path), "text": text.replace(old, new)}, desk)


async def _search(args: dict) -> str:
    """Through a whole tree: only inside the fence, which hides the secrets."""
    work = Path.home() / WORK
    argv = ["grep", "-rIn", "--", str(args.get("text") or ""), str(_path(args.get("path")))]
    fenced = fence.wrap(argv, work)
    if fenced is None:
        return "Failed: no fence on this machine, so no search through a tree."
    return await asyncio.to_thread(_exec, fenced)


def _notes(folder: str, name: str) -> Path:
    path = Path.home() / WORK / folder / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def plan_of(task: str) -> Path:
    return _notes(".plans", hashlib.sha256(task.encode()).hexdigest()[:16])


def recipes() -> str:
    """What was learned before; a line each, for the model to read on."""
    folder = Path.home() / WORK / ".recipes"
    return "\n".join(f"- {p.stem}: {p.read_text(encoding='utf-8')[:300]}"
                     for p in sorted(folder.glob("*.md")))[:3000] if folder.is_dir() else ""


async def _deep(command: str, desk: hand_life.Desk, net: bool = False) -> str:
    """Inside the fence a shell is safe enough to need no yes: its worst is
    the work folder. With no fence it is an ordinary command, and asked."""
    work = Path.home() / WORK
    if net and not await desk.permit(f"network: {command}", "change", command):
        return "Not done: no permission."
    fenced = fence.wrap(["/bin/sh", "-c", command], work, net=net)
    if fenced is not None:
        return await asyncio.to_thread(_exec, fenced, DEEP_RUN_S, work)
    if not await desk.permit(command, "change", command):
        return "Not done: no permission."
    return await asyncio.to_thread(_exec, ["/bin/sh", "-c", command], DEEP_RUN_S, work)


async def _run(args: dict, desk: hand_life.Desk, deep: bool = False) -> str:
    command = str(args.get("command") or "").strip()
    if deep and command:
        return await _deep(command, desk, str(args.get("network")).lower() == "yes")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"Failed: {exc}"
    if not argv:
        return "Failed: no command."
    if is_free(argv):
        # What is only looking stays only looking, by the system's hand too.
        fenced = fence.wrap(argv, Path.home() / WORK)
        if fenced is not None:
            return await asyncio.to_thread(_exec, fenced)
    if not await desk.permit(command, "change", command):
        return "Not done: no permission."
    return await asyncio.to_thread(_exec, argv)


async def run(name: str, args: dict, desk: hand_life.Desk, *, deep: bool = False,
              task: str = "") -> str:
    desk.step(name)
    if name == EDIT:
        return await _edit(args, desk)
    if name == SEARCH:
        return await _search(args)
    if name == PLAN:
        text = str(args.get("text") or "")
        plan_of(task).write_text(text, encoding="utf-8")
        now = next((line for line in text.splitlines() if line.startswith("- [ ]")), "")
        desk.step(now[5:].strip()[:80] or "plan kept")
        return "Kept."
    if name == RECIPE:
        title = re.sub(r"[^\w-]+", "-", str(args.get("title") or "recipe"))[:40]
        _notes(".recipes", title).write_text(str(args.get("text") or ""), encoding="utf-8")
        return "Kept."
    if name == LIST:
        return await _list(args)
    if name == READ:
        return await _read(args)
    if name == WRITE:
        return await _write(args, desk)
    return await _run(args, desk, deep)


def _tool(name: str, about: str, **fields: str) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": about,
        "parameters": {"type": "object", "required": [k for k in fields if k != "network"], "properties": {
            key: {"type": "string", "description": words}
            for key, words in fields.items()}}}}


SCHEMAS = [
    _tool(EDIT, "Replace one exact piece of a text file; it must occur once. "
                "Outside ~/heare-work the person is asked first.",
          path="The file.", old="The exact piece.", new="What takes its place."),
    _tool(SEARCH, "Find text through a whole folder; secrets stay hidden.",
          text="The text.", path="The folder; ~ is allowed."),
    _tool(PLAN, "Keep the plan of long work as lines '- [ ] step' and '- [x] step'. "
                "The first open step is shown to the person; after a failure the "
                "work continues from it.", text="The whole plan."),
    _tool(RECIPE, "Keep how a kind of task was solved, for next time.",
          title="A short name.", text="The steps that worked."),
    _tool(LIST, "List a folder on this machine.", path="The folder; ~ is allowed."),
    _tool(READ, "Read a text file on this machine.", path="The file; ~ is allowed."),
    _tool(WRITE, "Write a text file on this machine; the person is asked first.",
          path="The file; ~ is allowed.", text="The whole new content."),
    _tool(RUN, "Run one command on this machine, with no shell: no pipes, no "
               "redirects. Looking commands (ls, cat, grep, find, df, ps, git "
               "status…) run at once; any other is asked of the person first.",
          command="The command line.", network="yes, in deep mode, for the internet; else no."),
]
