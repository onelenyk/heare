"""Cheap readings of what the person is doing: the front app, and idleness.

Free, and safe to fail: each sensor returns a neutral value instead of
raising, because this runs on a timer beside a live conversation.
"""

from __future__ import annotations

import subprocess

# Applications it never looks at: not redacted, never named. A watcher that
# reads a password manager is a keylogger with better manners.
PRIVATE_APPS = frozenset({
    "1Password", "1Password 7", "Bitwarden", "KeePassXC", "Keychain Access",
    "Зв'язка ключів", "Passwords", "Паролі",
})


def front_app() -> str:
    """The application in front, or "" when unknown or private."""
    try:
        from AppKit import NSWorkspace

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        name = str(front.localizedName() or "") if front is not None else ""
    except Exception:  # noqa: BLE001 — a sensor that can crash the brain is worse than none
        return ""
    return "" if name in PRIVATE_APPS else name


def idle_seconds() -> float:
    """Seconds since the last key or mouse event, from the HID system."""
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-r", "-d", "1"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0.0
    for line in out.splitlines():
        if "HIDIdleTime" in line:
            try:
                return int(line.rsplit("=", 1)[1].strip()) / 1e9
            except (ValueError, IndexError):
                return 0.0
    return 0.0
