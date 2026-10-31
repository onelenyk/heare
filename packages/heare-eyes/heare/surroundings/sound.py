"""Which other programs are putting out sound right now.

Asked of CoreAudio, which keeps a list of processes and whether each has
output running. The obvious signal — the system's «audio is playing»
assertion — is useless here: the assistant keeps its own speaker open,
so that one always says yes. This one names who, and the assistant
leaves itself out.

Free: no permission, no model, a few milliseconds. Anywhere but macOS
the answer is «nothing is playing»; on a failure it is ``None``, meaning
«unknown», not «quiet».
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import struct
import subprocess
import sys

logger = logging.getLogger("heare.surroundings")

_SYSTEM = 1
_library = None
# The voice runs as `python -m heare.voice` in the app and as a
# `heare-voice` tool script after install.sh; both are our own body.
_OURS = re.compile(r"(?:-m\s+heare(?:[.\s]|$)|(?:^|[/\\])heare[-_]voice(?:\s|$))")


def _code(four: str) -> int:
    return struct.unpack(">I", four.encode())[0]


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


def _get(obj: int, selector: str) -> bytes | None:
    address = _Address(_code(selector), _code("glob"), 0)
    size = ctypes.c_uint32(0)
    if _library.AudioObjectGetPropertyDataSize(obj, ctypes.byref(address), 0, None,
                                               ctypes.byref(size)):
        return None
    data = (ctypes.c_char * size.value)()
    if _library.AudioObjectGetPropertyData(obj, ctypes.byref(address), 0, None,
                                           ctypes.byref(size), data):
        return None
    return bytes(data)


def _command(pid: int) -> str | None:
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _processes() -> dict[int, str] | None:
    """The command line of every process, asked once for the whole round."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not list the processes: %s", exc)
        return None
    found: dict[int, str] = {}
    for line in out.splitlines():
        head, _, command = line.strip().partition(" ")
        if head.isdigit():
            found[int(head)] = command.strip()
    return found


def _ours(pid: int, command: str | None = None) -> bool:
    """Whether this is one of the assistant's own bodies.

    The voice keeps its output device open, so CoreAudio reports it as
    running output even when it is silent — the eyes then tell the brain
    that something is playing, and every unaddressed phrase is dropped as
    room noise. Its own bodies are not other programs.
    """
    if command is None:
        command = _command(pid)
        if command is None:
            return False
    return bool(_OURS.search(command))


def _label(command: str) -> str:
    found = re.search(r"/([^/]+)\.app/", command)
    if found:
        name = found.group(1)
    else:
        first = command.split(None, 1)[0] if command.strip() else ""
        name = os.path.basename(first)
    # «Google Chrome Helper (Renderer)» is Chrome to the person.
    return name.split(" Helper")[0]


def _name(pid: int, command: str | None = None) -> str:
    if command is not None:
        return _label(command)
    try:
        path = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return os.path.basename(path).split(" Helper")[0]


def playing() -> list[str] | None:
    """Names of the other programs with sound output running, sorted.

    ``None`` when the process list or CoreAudio could not be read: an
    unknown room must not be mistaken for silence.
    """
    global _library
    if sys.platform != "darwin":
        return []
    try:
        if _library is None:
            _library = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
        commands = _processes()
        if commands is None:
            return None
        listed = _get(_SYSTEM, "prs#")
        if not listed:
            return []
        names: set[str] = set()
        for obj in struct.unpack(f"{len(listed) // 4}I", listed):
            pid, out = _get(obj, "ppid"), _get(obj, "piro")
            if not pid or not out or not struct.unpack("I", out)[0]:
                continue
            pid = struct.unpack("i", pid)[0]
            command = commands.get(pid)
            if command is None:
                continue
            if pid != os.getpid() and not _ours(pid, command) and (name := _name(pid, command)):
                names.add(name)
        return sorted(names)
    except Exception as exc:  # noqa: BLE001 — a sense that fails reports unknown, never breaks
        logger.warning("could not read what is playing: %s", exc)
        return None
