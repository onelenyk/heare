"""Whether it can still hear, and saying so when it cannot.

Four times in two days this project shipped something that looked alive
and was not. A build whose PortAudio was missing: the daemon started,
served its dashboard, answered `/state` with 200, and raised "PortAudio
library not found" into a log nobody was reading. A process that ran all
night: the machine slept, the input device went away, and thirty-five
identical `-9986` retries went by while the menu bar sat there looking
normal. A bundle with no roles: an empty directory, a log line reading
`spine roles loaded:` with nothing after the colon, and every trigger
phrase silently never matching.

The specific bugs are all different. The shape is one shape: **the
failure is visible from the inside and invisible from the outside.**

Fixing them one at a time closes one case each. This closes the class.
It does not know why the microphone stopped — it does not need to. It
knows the device has not called in twenty seconds, that nobody muted it,
and that a person believing they are being heard is at this moment
talking to nothing. That is enough to say something out loud.

Two things it must not do
-------------------------
* **Never confuse a mute with a fault.** Silence you asked for is not a
  failure, and an assistant that announces its own mute is worse than
  one that says nothing. The device keeps calling back while muted —
  which is precisely why the timestamp is taken before that check.

* **Never nag.** One outage is one thing to say. It is raised through
  the engine like anything else, so being brushed off costs it the same
  patience, and a `dedupe_key` tied to when the silence began means a
  fault lasting an hour is still one remark.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("spine.hearing")

# How long the device may go without calling before something is wrong.
# Frames arrive every few tens of milliseconds, so this is three orders
# of magnitude past normal — long enough that no scheduling hiccup or
# device switch trips it, short enough that you have not yet given up on
# the conversation and walked away.
SILENT_AFTER_S = 20.0


@dataclass(frozen=True)
class Hearing:
    """One reading of the ear. Immutable, computed whole."""

    wired: bool  # is there an audio front end at all (a --text run has none)
    stream_open: bool  # did an input stream survive boot
    silent_for: float  # since the device last called back
    muted: bool  # by a person, on purpose

    @property
    def deaf(self) -> bool:
        """Not hearing, and not because anyone asked for that."""
        if not self.wired or self.muted:
            return False
        if not self.stream_open:
            return True
        return self.silent_for >= SILENT_AFTER_S

    def describe(self) -> str:
        """What to say out loud. In the first person, because it is the
        assistant's own fault being reported, and because "the input
        stream is not open" is not a sentence anyone should have to hear
        from something that talks."""
        if not self.stream_open:
            return "я не чую — мікрофон не відкрився. Мене треба перезапустити."
        return (
            f"я перестала чути {int(self.silent_for)} секунд тому — "
            "схоже, зник мікрофон. Мене треба перезапустити."
        )


class EarWatch:
    """Holds the one thing that has to outlive a reading: whether this
    outage has already been mentioned.

    Re-arms when hearing comes back, so a second fault an hour later is
    said again — the first remark was about the first outage, and a
    person who fixed one has no reason to assume the next.
    """

    def __init__(self) -> None:
        self._said = False

    def feed(self, hearing: Hearing, *, now: float) -> tuple[str, str] | None:
        """A reading in, and either something to say or nothing.

        Returns ``(text, dedupe_key)``. The key is tied to when the
        silence began rather than to now, so the same outage cannot
        become two remarks if this is asked twice.
        """
        if not hearing.deaf:
            if self._said:
                logger.info("hearing: the microphone is back")
            self._said = False
            return None
        if self._said:
            return None
        self._said = True
        began = int(now - hearing.silent_for)
        logger.error(
            "hearing: deaf — stream_open=%s silent_for=%.0fs",
            hearing.stream_open,
            hearing.silent_for,
        )
        return hearing.describe(), f"deaf:{began}"

    def forget(self) -> None:
        self._said = False


def read(audio: object | None, *, muted: bool = False) -> Hearing:
    """Ask the audio front end how it is doing.

    Absorbs everything: a watchdog that can raise is one more thing that
    can take the conversation down, and the whole point of it is to be
    the part that still works when other parts do not.
    """
    if audio is None:
        return Hearing(wired=False, stream_open=False, silent_for=0.0, muted=False)
    try:
        return Hearing(
            wired=True,
            stream_open=bool(getattr(audio, "input_open", False)),
            silent_for=float(audio.silent_for()),  # type: ignore[attr-defined]
            muted=bool(muted or getattr(audio, "mute_input_user", False)),
        )
    except Exception:  # noqa: BLE001
        logger.exception("hearing: could not read the ear (non-fatal)")
        return Hearing(wired=False, stream_open=False, silent_for=0.0, muted=False)


__all__ = ["EarWatch", "Hearing", "SILENT_AFTER_S", "read"]
