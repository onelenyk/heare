"""The present, as one small object you can print.

The pieces were always there and never gathered: the clock is a line at
the end of the prompt, ``agent_state`` and ``voice_state`` carry a
``since_ts`` nobody reads for this, the jobs know what is running, and
how long you have been quiet was known to nothing at all.

Scattered, they answer nothing. Together they are the difference between
an assistant that reacts and one that is somewhere: it is late, you have
been quiet for forty minutes, the last thing that happened was a job
finishing, and it has not spoken unbidden in three hours.

Two properties are deliberate:

* **Immutable, and computed whole.** Nothing reads half a situation.
* **No clock inside.** ``now`` is passed in. That is what turns the
  engine's judgement into a pure function — every rule about when to
  speak becomes a table of cases in a test file instead of something you
  have to sit and listen for.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

# Night is not a preference. Being loud at 02:00 is the kind of thing
# that gets a device switched off for good.
NIGHT_FROM = 22
NIGHT_UNTIL = 7

# What an unknown gap means. Large, so that missing data reads as
# "nobody is known to be here" rather than "they just spoke".
UNKNOWN_SILENCE_S = 1e9

# What an unknown keyboard reads as. Same shape as the silence sentinel
# and for the same reason: no reading must never be mistaken for someone
# sitting there.
UNKNOWN_IDLE_S = 1e9

# How recently the keyboard has to have been touched to count as being
# at the desk. Generous on purpose — reading a screen is not absence,
# and the cost of being wrong here is only that it may speak to someone
# who stepped out for coffee.
AT_KEYBOARD_S = 5 * 60.0

# The phases ``agent_state`` can hold, split by whether a remark landing
# now would be an interruption. Two vocabularies live here: the daemon
# writes idle / talking / interrupted, the old engine wrote listening /
# thinking / speaking, and for months this file only knew the second —
# so the guard against speaking mid-turn could not fire.
#
# Every phase any writer produces must appear in one of these two sets.
# That is not a style rule: a phase the reader has never heard of falls
# through to "not busy", which is the answer that talks over you.
# tests/test_spine_situation.py reads the daemon's literals and fails if
# one is missing from both.
BUSY_STATES = frozenset({"talking", "listening", "thinking", "speaking"})
QUIET_STATES = frozenset({"idle", "interrupted", "unknown"})


# Where it is. Read once at import: the machine does not move, and a
# hostname lookup on every tick would be a syscall for a constant.
def _where() -> str:
    """One phrase for the place, in the language the assistant speaks.

    Not decoration. Told only the hour, a model reasons about time as a
    number; told it is night on a laptop in Kyiv, it reasons about a
    person who is probably tired and probably alone. The prompt already
    carried a timestamp — the part that was missing is everything that
    makes a timestamp mean something.
    """
    import platform

    host = " ".join(platform.node().split(".")[0].replace("-", " ").split())
    tz = time.tzname[time.daylight and time.localtime().tm_isdst > 0]
    system = {"Darwin": "макбук", "Linux": "linux", "Windows": "windows"}.get(
        platform.system(), platform.system() or "невідомо"
    )
    return f"{system}, {host} ({tz})" if host else f"{system} ({tz})"


PLACE = _where()

_WEEKDAYS = (
    "понеділок", "вівторок", "середа", "четвер",
    "пʼятниця", "субота", "неділя",
)


@dataclass(frozen=True)
class Situation:
    """Everything the engine is allowed to know about now."""

    now: float
    hour: int
    minute: int
    weekday: int  # 0 = Monday, as time.localtime gives it

    # Between us
    silence_s: float  # since anything was said, by either
    user_silence_s: float  # since the user last said something

    # What it is doing
    bot_state: str  # idle / talking / interrupted
    bot_state_s: float
    jobs_running: int

    # Its own recent forwardness
    unprompted_last_s: float  # since it last spoke unbidden
    unprompted_1h: int  # how often it has, this hour

    # Whether anyone is at the desk at all — a different question from
    # the one above, since most of a working day is spent not talking.
    # Last, and defaulted, so every existing caller keeps working and
    # gets the honest answer: not known.
    idle_s: float = UNKNOWN_IDLE_S  # since the last key or mouse event

    @property
    def is_night(self) -> bool:
        return self.hour >= NIGHT_FROM or self.hour < NIGHT_UNTIL

    @property
    def user_is_here(self) -> bool:
        """Is there anyone to hear it.

        Two ways of knowing, and the second was missing. Speech alone
        answers "did they talk to me lately", which is not the question:
        work an hour in silence and the engine concluded the room was
        empty, held everything it had and said nothing. That is exactly
        the hour a proactive assistant exists for.

        The keyboard closes it. `HIDIdleTime` costs one 16 ms subprocess,
        needs no permission and records nothing — it says only whether a
        key or the mouse was touched, never which. Unknown stays unknown:
        with no reading at all `idle_s` is the sentinel, and presence
        falls back to speech alone.
        """
        return self.user_silence_s < 300 or self.idle_s < AT_KEYBOARD_S

    @property
    def busy_talking(self) -> bool:
        """Mid-turn: anything said now would be an interruption, not a
        remark.

        The names here are two vocabularies, deliberately. The daemon
        stamps ``agent_state`` as idle / talking / interrupted
        (spine_engine.py ``_agent``); the old engine wrote listening /
        thinking / speaking, and this property was written against that
        one. So it looked for three words nothing had produced in months
        and answered False through the middle of every sentence. Both
        sets are listed rather than one translated into the other: the
        cost of an extra string is nothing, and the cost of this guard
        silently not firing is that it talks over you.
        """
        return self.bot_state in BUSY_STATES

    def describe(self) -> str:
        """One line, for the prompt and for the log.

        Written the way a person would say it — weekday, clock, part of
        day — rather than as a timestamp. "2026-08-18 01:08:00" is a fact
        a model has to decode before it can use; "вівторок, 01:08, ніч"
        is one it can act on.
        """
        parts = [
            f"{_WEEKDAYS[self.weekday % 7]}, "
            f"{self.hour:02d}:{self.minute:02d}, {_clock(self.hour)}"
        ]
        # An unknown gap is not a long one. Printing the sentinel would
        # tell the model the room has been quiet for thirty years.
        if 60 < self.silence_s < UNKNOWN_SILENCE_S:
            parts.append(f"тиша {_span(self.silence_s)}")
        if self.jobs_running:
            parts.append(f"в роботі: {self.jobs_running}")
        parts.append(PLACE)
        return ", ".join(parts)


def _clock(hour: int) -> str:
    if hour >= NIGHT_FROM or hour < NIGHT_UNTIL:
        return "ніч"
    if hour < 12:
        return "ранок"
    if hour < 18:
        return "день"
    return "вечір"


def _span(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} хв"
    if seconds < 86400:
        return f"{int(seconds // 3600)} год"
    return f"{int(seconds // 86400)} дн"


def _since(raw: str | None, now: float) -> tuple[str, float]:
    """Read a ``{"state": ..., "since_ts": ...}`` blob out of State.

    Both keys are written by the pipeline as JSON strings; a missing or
    malformed one must not be able to stop the engine, so it degrades to
    "unknown, just now" rather than raising.
    """
    if not raw:
        return "unknown", 0.0
    try:
        blob = json.loads(raw)
        return str(blob.get("state", "unknown")), max(
            0.0, now - float(blob.get("since_ts", now))
        )
    except (ValueError, TypeError):
        return "unknown", 0.0


async def observe(
    *,
    state: Any = None,
    persist: Any = None,
    jobs: Any = None,
    unprompted_last_ts: float = 0.0,
    unprompted_times: list[float] | None = None,
    idle: Any = None,
    now: float | None = None,
) -> Situation:
    """Gather the present from what is already being recorded.

    Every source is optional and every failure is absorbed: a situation
    with a hole in it is still worth having, and this runs on a timer
    beside a live conversation.

    ``idle`` is the one sensor here — an async callable returning seconds
    since the keyboard was last touched. Injected rather than imported so
    this module keeps no opinion about where the number comes from, and
    so a test can hand it a constant.
    """
    now = now if now is not None else time.time()
    stamp = time.localtime(now)

    bot_state, bot_state_s = "unknown", 0.0
    if state is not None:
        try:
            bot_state, bot_state_s = _since(state.get("agent_state"), now)
        except Exception:  # noqa: BLE001
            pass

    # Unknown must not read as "just spoke". With no record at all the
    # honest answer is that nobody is known to be here — an engine that
    # assumes presence from missing data would treat a fresh install, or
    # a failed query, as an invitation.
    silence_s = user_silence_s = UNKNOWN_SILENCE_S
    if persist is not None:
        try:
            last = persist.last_turn_times()
            if last.get("any"):
                silence_s = max(0.0, now - last["any"])
            if last.get("user"):
                user_silence_s = max(0.0, now - last["user"])
        except Exception:  # noqa: BLE001
            pass

    # Absorbed like the rest: a sensor that cannot answer leaves the
    # sentinel, and presence goes back to being judged by speech alone.
    idle_s = UNKNOWN_IDLE_S
    if idle is not None:
        try:
            idle_s = max(0.0, float(await idle()))
        except Exception:  # noqa: BLE001
            pass

    running = 0
    if jobs is not None:
        try:
            running = await jobs.running_count()
        except Exception:  # noqa: BLE001
            pass

    recent = [t for t in (unprompted_times or []) if now - t < 3600]

    return Situation(
        now=now,
        hour=stamp.tm_hour,
        minute=stamp.tm_min,
        weekday=stamp.tm_wday,
        silence_s=silence_s,
        user_silence_s=user_silence_s,
        bot_state=bot_state,
        bot_state_s=bot_state_s,
        jobs_running=running,
        unprompted_last_s=max(0.0, now - unprompted_last_ts) if unprompted_last_ts else 1e9,
        unprompted_1h=len(recent),
        idle_s=idle_s,
    )
