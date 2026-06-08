"""Cross-platform helpers for managing a CDP-attachable Chrome instance.

The watch dashboard exposes a "launch debug Chrome" hotkey that calls
:func:`ensure_debug_chrome`. The function probes the debug port first
(no-op if Chrome is already reachable), then spawns Chrome detached
from the dashboard process so closing the TUI does not kill the
browser.

Failure modes are reported via the returned ``str`` so the dashboard
can render them in the controls bar — never raised — because launching
a browser is a side-effect the user may want to retry from a different
state.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_PORT = 9222
_PROBE_TIMEOUT_S = 0.3
_LAUNCH_WAIT_TICKS = 20  # × 0.25 s = 5 s
_LAUNCH_WAIT_INTERVAL_S = 0.25


def is_debug_reachable(port: int = _DEFAULT_PORT) -> bool:
    """Return True iff Chrome's CDP HTTP endpoint responds on *port*."""
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=_PROBE_TIMEOUT_S
        ).read()
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
    return True


def _chrome_command(port: int, profile_directory: str | None) -> list[str] | None:
    """Resolve the Chrome / Chromium binary for the current platform.

    Always pass ``--profile-directory`` when one is supplied, even on a
    single-profile install — Chrome's "who's using Chrome?" picker
    silently drops ``--remote-debugging-port`` when the user picks a
    profile, so naming the profile up-front is the only reliable way
    to keep CDP attached.
    """
    args = [f"--remote-debugging-port={port}", "--no-first-run"]
    if profile_directory:
        args.append(f"--profile-directory={profile_directory}")

    if sys.platform == "darwin":
        macos_binary = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(macos_binary):
            return [macos_binary, *args]
        return None

    # Linux — try the most common binary names in order.
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return [path, *args]
    return None


def ensure_debug_chrome(
    port: int = _DEFAULT_PORT, profile_directory: str | None = None
) -> str:
    """Make sure a Chrome with CDP attached is reachable on *port*.

    Returns a one-line human status the dashboard renders verbatim.
    """
    if is_debug_reachable(port):
        return f"chrome already attached on :{port}"

    cmd = _chrome_command(port, profile_directory)
    if cmd is None:
        return "chrome binary not found (install Chrome / Chromium)"

    try:
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return f"chrome launch failed: {exc}"

    # Give Chrome a few seconds to open the debug port.
    for _ in range(_LAUNCH_WAIT_TICKS):
        time.sleep(_LAUNCH_WAIT_INTERVAL_S)
        if is_debug_reachable(port):
            return f"chrome launched, attached on :{port}"

    # Most common reason: Chrome was already running without the debug
    # flag, so the new launch attached to the existing user-data-dir
    # and exited without opening the port.
    return "chrome did not open debug port (quit Chrome and retry)"


@dataclass(frozen=True)
class ChromeProfile:
    """One Chrome profile entry surfaced from the user-data ``Local State`` file.

    ``directory`` is the directory name passed to ``--profile-directory``
    (e.g. ``"Default"``, ``"Profile 1"``); ``name`` and ``email`` are the
    human-friendly fields Chrome stores per profile (may be empty).
    """

    directory: str
    name: str
    email: str
    last_used: bool


def _chrome_user_data_dir() -> Path | None:
    """Default Chrome user-data root for the current platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / "google-chrome"
    return None


def list_chrome_profiles() -> list[ChromeProfile]:
    """Read the Chrome ``Local State`` file and return the available profiles.

    Returns an empty list when Chrome has never run (or the file is
    unreadable / corrupt). Sorted with the last-used profile first, then
    alphabetically by directory name so the picker has a stable order.
    """
    root = _chrome_user_data_dir()
    if root is None:
        return []
    state_file = root / "Local State"
    try:
        raw = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return []

    profile = raw.get("profile", {})
    info_cache: dict = profile.get("info_cache", {}) or {}
    last_used = profile.get("last_used", "Default")

    profiles: list[ChromeProfile] = []
    for directory, info in info_cache.items():
        if not (root / directory).is_dir():
            continue
        profiles.append(
            ChromeProfile(
                directory=directory,
                name=str(info.get("name") or ""),
                email=str(info.get("user_name") or ""),
                last_used=(directory == last_used),
            )
        )

    profiles.sort(key=lambda p: (not p.last_used, p.directory.lower()))
    return profiles


__all__ = [
    "ChromeProfile",
    "ensure_debug_chrome",
    "is_debug_reachable",
    "list_chrome_profiles",
]
