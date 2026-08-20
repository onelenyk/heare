"""What is happening around it, sampled cheaply enough to do constantly.

``situation.py`` answers "when and where am I". This answers "what is the
person doing" — and it is the half that was missing. The engine could
already judge when it may speak; its only event sources were the
assistant reporting on itself, so it had nothing to speak about.

Three properties, all deliberate:

* **Free.** Every sensor here is either in-process (AppKit, already
  installed — ``rumps`` brought pyobjc for the menu bar) or one 16 ms
  subprocess. Nothing here needs a permission dialog, a paid API, or a
  model call. The expensive senses — the accessibility tree, a screen
  capture — open on an event these produce, never on a timer.

* **Changes, not states.** "Chrome is open" is noise. "You left the thing
  you had been on for three hours" is not. ``changes()`` is a pure
  function of two snapshots, so every rule about what deserves a remark
  is a table of cases in a test file rather than an afternoon of sitting
  and watching.

* **It does not read the clipboard.** ``NSPasteboard.changeCount()``
  reports that the pasteboard changed without disclosing a byte of it.
  A watcher that reads what you copy is a keylogger with better manners;
  knowing *that* you copied is enough to decide whether to look closer,
  and looking closer should be a decision, not a default.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger("heare.spine.environment")

# Applications it does not look at, at all. Not "redacts afterwards" —
# never records the name, never emits a change. The list is short and
# obvious on purpose: the point is that it exists and is enforced in one
# place, so adding to it is a one-line decision.
PRIVATE_APPS: frozenset[str] = frozenset(
    {
        "1Password",
        "1Password 7",
        "Bitwarden",
        "KeePassXC",
        "Keychain Access",
        "Зв'язка ключів",
        "Passwords",
        "Паролі",
    }
)

PRIVATE = "—"  # what the front app reads as while it is one of the above

# How long counts as having been *in* something, rather than passing
# through it. Below this, a switch is just navigation and not worth a word.
SETTLED_S = 20 * 60.0

# How long counts as being deep in one thing. Fires once per stretch.
DEEP_S = 90 * 60.0

# Long enough away that coming back is an event.
AWAY_S = 15 * 60.0

# Back at the keyboard.
PRESENT_S = 60.0

# How long a remark about the surroundings stays true enough to make.
# Said fifteen minutes late it is not a stale remark, it is a false one.
FRESH_S = 15 * 60.0


@dataclass(frozen=True)
class Environment:
    """One reading of the surroundings. Immutable, computed whole."""

    now: float
    app: str = ""  # front application, or PRIVATE, or "" when unknown
    apps: tuple[str, ...] = ()  # applications with windows
    idle_s: float = 0.0  # since the last key or mouse event
    clipboard_seq: int = 0  # NSPasteboard.changeCount() — never the content

    @property
    def private(self) -> bool:
        return self.app == PRIVATE


@dataclass(frozen=True)
class Change:
    """Something worth possibly saying. Not yet a decision to say it —
    the engine's judge still has to agree, and after that the model still
    gets to refuse."""

    kind: str
    text: str
    urgency: float
    dedupe_key: str
    # How long this stays worth saying. What you were doing is a remark
    # about the present tense; heard an hour late it is not a smaller
    # version of itself, it is wrong. The engine turns this into an
    # intent that expires rather than one that waits.
    ttl_s: float = FRESH_S


# ── sensors ───────────────────────────────────────────────────────────
#
# Each one absorbs its own failure and returns the neutral value. This
# runs on a timer beside a live conversation; a watcher that can crash
# the assistant is worse than no watcher.


def _front_and_apps() -> tuple[str, tuple[str, ...]]:
    try:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        front = workspace.frontmostApplication()
        name = str(front.localizedName() or "") if front is not None else ""
        # activationPolicy 0 == NSApplicationActivationPolicyRegular, i.e.
        # it has a Dock icon and windows. Agents and daemons are not
        # things a person is "in".
        apps = tuple(
            str(a.localizedName() or "")
            for a in workspace.runningApplications()
            if a.activationPolicy() == 0 and a.localizedName()
        )
    except Exception:  # noqa: BLE001
        return "", ()
    if name in PRIVATE_APPS:
        return PRIVATE, tuple(a for a in apps if a not in PRIVATE_APPS)
    return name, tuple(a for a in apps if a not in PRIVATE_APPS)


def _idle_seconds() -> float:
    """Seconds since the last key or mouse event, from the HID system.

    16 ms of subprocess, which is why it is called from a thread. There is
    an in-process route through IOKit, but that needs a pyobjc framework
    this project does not install, and this is not worth an install.
    """
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-r", "-d", "1"],
            capture_output=True,
            text=True,
            timeout=3,
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


def _clipboard_seq() -> int:
    """The pasteboard's change counter — not its contents.

    This is the whole privacy design in one call: it goes up when you copy
    something and tells nobody what."""
    try:
        from AppKit import NSPasteboard

        return int(NSPasteboard.generalPasteboard().changeCount())
    except Exception:  # noqa: BLE001
        return 0


async def observe(*, now: float, in_thread: Any = None) -> Environment:
    """Read the surroundings once.

    ``now`` is passed in for the same reason ``Situation`` takes it: with
    no clock inside, every rule downstream becomes a pure function.
    """
    to_thread = in_thread or asyncio.to_thread
    try:
        app, apps = await to_thread(_front_and_apps)
        idle_s = await to_thread(_idle_seconds)
        clipboard_seq = await to_thread(_clipboard_seq)
    except Exception:  # noqa: BLE001
        logger.exception("environment: sampling failed (non-fatal)")
        return Environment(now=now)
    return Environment(
        now=now, app=app, apps=apps, idle_s=idle_s, clipboard_seq=clipboard_seq
    )


# ── what is worth noticing ────────────────────────────────────────────


def changes(
    prev: Environment | None,
    cur: Environment,
    *,
    app_since: float,
    deep_said: bool = False,
) -> list[Change]:
    """Two readings in, remarks out. Pure — no clock, no I/O.

    Three rules, and the restraint is the point. Ninety-nine percent of
    what a watcher sees deserves no words, and one that comments on every
    window switch gets turned off within the hour. So: leaving something
    you had really been *in*, coming back after being properly away, and
    still being in one thing after an hour and a half. Everything else it
    sees and says nothing about.

    ``app_since`` is when the current front application became front, and
    ``deep_said`` whether the "still in it" remark has already been made
    for this stretch — both live in the caller because they outlive a
    single reading.
    """
    if prev is None or cur.private or prev.private:
        return []

    out: list[Change] = []

    # Left something it had been in for a long while.
    if cur.app and prev.app and cur.app != prev.app:
        held = max(0.0, prev.now - app_since)
        if held >= SETTLED_S:
            out.append(
                Change(
                    kind="switched",
                    text=f"ти був у {prev.app} {_span(held)}, тепер {cur.app}",
                    urgency=0.35,
                    dedupe_key=f"switched:{prev.app}:{int(app_since)}",
                )
            )

    # Back at the keyboard after being properly away. Reported against
    # the previous reading, because the idle counter resets the moment a
    # key is touched and the gap would otherwise be invisible.
    if prev.idle_s >= AWAY_S and cur.idle_s < PRESENT_S:
        out.append(
            Change(
                kind="returned",
                text=f"тебе не було {_span(prev.idle_s)}",
                urgency=0.3,
                dedupe_key=f"returned:{int(prev.now)}",
            )
        )

    # Still in one thing, well past the point where that is worth naming.
    # Only while someone is actually there, and only if they were there
    # for the whole of it — both readings. An application left open
    # through lunch has not been held for three hours, and the moment of
    # coming back is exactly when the naive version fired: the clock had
    # been running the entire absence. "Без перерви" said about two hours
    # away is not a small inaccuracy; it is the assistant claiming to
    # have watched something it did not.
    if (
        not deep_said
        and cur.app
        and cur.app == prev.app
        and prev.idle_s < AWAY_S
        and cur.idle_s < AWAY_S
    ):
        held = max(0.0, cur.now - app_since)
        if held >= DEEP_S:
            out.append(
                Change(
                    kind="deep",
                    text=f"ти {_span(held)} в {cur.app} без перерви",
                    urgency=0.4,
                    dedupe_key=f"deep:{cur.app}:{int(app_since)}",
                )
            )

    return out


def _span(seconds: float) -> str:
    """A duration the way a person says it.

    Half-hour granularity above the hour on purpose: "годину" for ninety
    minutes is the kind of small inaccuracy that makes an assistant sound
    like it was not really paying attention — which, for a watcher, is
    exactly the impression it cannot afford.
    """
    if seconds < 3600:
        return f"{int(seconds // 60)} хв"
    halves = int(seconds / 1800)  # half-hours, rounded down
    if halves == 2:
        return "годину"
    if halves == 3:
        return "півтори години"
    if halves % 2 == 1:
        return f"{halves // 2} з половиною год"
    return f"{halves // 2} год"


class EnvironmentWatch:
    """Holds the little that has to outlive one reading.

    Namely: the previous snapshot, when the current application became
    front, and whether the "still in it" remark has been spent on this
    stretch. Everything else is recomputed, and every rule that decides
    anything lives in ``changes``, which this only feeds.
    """

    def __init__(self) -> None:
        self.previous: Environment | None = None
        self.app_since: float = 0.0
        self._deep_said = False

    def feed(self, sample: Environment) -> list[Change]:
        # A reading with no front application is a sensor that failed,
        # not a person who left. Taking it as a reading would count as a
        # switch, restart the clock on the application and spend the
        # long-stretch remark — one hiccup erasing an hour and a half of
        # accumulated context.
        if not sample.app:
            return []

        previous = self.previous
        if previous is None:
            self.previous = sample
            self.app_since = sample.now
            return []

        found = changes(
            previous,
            sample,
            app_since=self.app_since,
            deep_said=self._deep_said,
        )
        came_back = previous.idle_s >= AWAY_S and sample.idle_s < PRESENT_S
        if sample.app != previous.app or came_back:
            # Coming back restarts the clock for the same reason a switch
            # does: what is being measured is time spent at the thing,
            # and being away is not that.
            self.app_since = sample.now
            self._deep_said = False
        if any(c.kind == "deep" for c in found):
            self._deep_said = True
        self.previous = sample
        return found

    def forget(self) -> None:
        """Drop everything held about the surroundings.

        A watcher that cannot be told to forget is not a watcher. This is
        the whole retention policy for now, and it is deliberately blunt:
        nothing here is written to disk, so forgetting is complete.
        """
        self.previous = None
        self.app_since = 0.0
        self._deep_said = False


__all__ = [
    "Change",
    "Environment",
    "EnvironmentWatch",
    "PRIVATE",
    "PRIVATE_APPS",
    "changes",
    "observe",
    "replace",
]
