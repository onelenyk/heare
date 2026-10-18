"""Which other programs are putting out sound right now.

Asked of CoreAudio, which keeps a list of processes and whether each has
output running. The obvious signal — the system's «audio is playing»
assertion — is useless here: the assistant keeps its own speaker open,
so that one always says yes. This one names who, and the assistant
leaves itself out.

Free: no permission, no model, a few milliseconds. Anywhere but macOS,
and on any failure, the answer is «nothing is playing».
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import struct
import subprocess
import sys

_SYSTEM = 1
_library = None
_OURS = re.compile(r"-m\s+heare(?:[.\s]|$)")


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


def _ours(pid: int) -> bool:
    """Whether this is one of the assistant's own bodies.

    The voice keeps its output device open, so CoreAudio reports it as
    running output even when it is silent — the eyes then tell the brain
    that something is playing, and every unaddressed phrase is dropped as
    room noise. Its own bodies are not other programs.
    """
    try:
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True,
                                 timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(_OURS.search(command))


def _name(pid: int) -> str:
    try:
        path = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    name = os.path.basename(path)
    # «Google Chrome Helper (Renderer)» is Chrome to the person.
    return name.split(" Helper")[0]


def playing() -> list[str]:
    """Names of the other programs with sound output running, sorted."""
    global _library
    if sys.platform != "darwin":
        return []
    try:
        if _library is None:
            _library = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
        listed = _get(_SYSTEM, "prs#")
        if not listed:
            return []
        names: set[str] = set()
        for obj in struct.unpack(f"{len(listed) // 4}I", listed):
            pid, out = _get(obj, "ppid"), _get(obj, "piro")
            if not pid or not out or not struct.unpack("I", out)[0]:
                continue
            pid = struct.unpack("i", pid)[0]
            if pid != os.getpid() and not _ours(pid) and (name := _name(pid)):
                names.add(name)
        return sorted(names)
    except Exception:  # noqa: BLE001 — a sense that fails reports nothing, never breaks
        return []
