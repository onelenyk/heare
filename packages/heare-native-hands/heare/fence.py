"""The fence: one promise, whatever the platform.

A fenced command reads what its owner reads except the secrets, writes only
to its work folder and has no network unless asked. The operating system
holds that, not a list of names. Each platform keeps the promise its own way;
where nothing can keep it, ``wrap`` says so with None and the caller must not
run the command as if it were fenced.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

SECRETS = (".ssh", ".gnupg", ".aws", ".netrc", ".config/gcloud",
           ".heare/machine", "Library/Keychains")


def _hidden(home: Path) -> list[Path]:
    return [home / name for name in SECRETS]


def _macos(argv: list[str], work: Path, home: Path, net: bool) -> list[str]:
    deny = " ".join(f'(subpath "{path}")' for path in _hidden(home))
    temp = Path("/private/tmp")
    rules = [
        "(version 1)", "(allow default)",
        "(deny file-write*)",
        f'(allow file-write* (subpath "{work.resolve()}") (subpath "{temp}") '
        '(literal "/dev/null") (literal "/dev/tty"))',
        f"(deny file-read* {deny})",
    ]
    if not net:
        rules.append("(deny network*)")
    return ["/usr/bin/sandbox-exec", "-p", "\n".join(rules), *argv]


def _linux(argv: list[str], work: Path, home: Path, net: bool) -> list[str]:
    fence = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
             "--tmpfs", "/tmp", "--bind", str(work), str(work), "--die-with-parent"]
    for path in _hidden(home):
        if path.exists():
            fence += ["--tmpfs", str(path)]
    if not net:
        fence.append("--unshare-net")
    return [*fence, "--", *argv]


def wrap(argv: list[str], work: Path, *, net: bool = False,
         home: Path | None = None, platform: str | None = None) -> list[str] | None:
    """The command inside the fence, or None where there is no fence."""
    home = home or Path.home()
    work.mkdir(parents=True, exist_ok=True)
    platform = platform or sys.platform
    if platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        return _macos(argv, work, home, net)
    if platform.startswith("linux") and shutil.which("bwrap"):
        return _linux(argv, work, home, net)
    return None
