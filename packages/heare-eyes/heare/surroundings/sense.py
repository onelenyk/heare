"""Cheap readings of what the person is doing: the front app, and idleness.

Free, and safe to fail: each sensor returns ``None`` for «unknown» instead
of raising, because this runs on a timer beside a live conversation. An
unknown reading is never turned into a quiet room: the caller leaves the
last known fact alone and says that the sensor is blind.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("heare.surroundings")

# Applications it never looks at: not redacted, never named. A watcher that
# reads a password manager is a keylogger with better manners.
PRIVATE_APPS = frozenset({
    "1Password", "1Password 7", "Bitwarden", "KeePassXC", "Keychain Access",
    "Зв'язка ключів", "Passwords", "Паролі",
})


def front_app() -> str | None:
    """The application in front, ``""`` when it must stay unnamed, or ``None``.

    ``None`` is the documented «unknown»: AppKit is missing, the workspace
    has no front window, or the read failed. It is not the same as «quiet».
    """
    try:
        from AppKit import NSWorkspace

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        name = str(front.localizedName() or "") if front is not None else ""
    except Exception as exc:  # noqa: BLE001 — a sensor that can crash the brain is worse than none
        logger.warning("could not read the front app: %s", exc)
        return None
    return "" if name in PRIVATE_APPS else name


def idle_seconds() -> float | None:
    """Seconds since the last key or mouse event, or ``None`` when unknown."""
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-r", "-d", "1"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not read idleness: %s", exc)
        return None
    for line in out.splitlines():
        if "HIDIdleTime" in line:
            try:
                return int(line.rsplit("=", 1)[1].strip()) / 1e9
            except (ValueError, IndexError) as exc:
                logger.warning("idleness is unreadable: %s", exc)
                return None
    logger.warning("idleness is missing from ioreg")
    return None
