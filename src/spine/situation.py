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


@dataclass(frozen=True)
class Situation:
    """Everything the engine is allowed to know about now."""

    now: float
    hour: int

    # Between us
    silence_s: float  # since anything was said, by either
    user_silence_s: float  # since the user last said something

    # What it is doing
    bot_state: str  # idle / listening / thinking / speaking
    bot_state_s: float
    mode: str
    jobs_running: int

    # Its own recent forwardness
    unprompted_last_s: float  # since it last spoke unbidden
    unprompted_1h: int  # how often it has, this hour

    @property
    def is_night(self) -> bool:
        return self.hour >= NIGHT_FROM or self.hour < NIGHT_UNTIL

    @property
    def user_is_here(self) -> bool:
        """Talked within the last few minutes."""
        return self.user_silence_s < 300

    @property
    def busy_talking(self) -> bool:
        """Mid-turn: anything said now would be an interruption, not a
        remark."""
        return self.bot_state in ("listening", "thinking", "speaking")

    def describe(self) -> str:
        """One line, for the prompt and for the log.

        This is the block that makes "what does it think is going on"
        answerable at all — before it, the only way to find out was to
        guess from behaviour.
        """
        parts = [f"{_clock(self.hour)}"]
        if self.silence_s > 60:
            parts.append(f"тиша {_span(self.silence_s)}")
        if self.jobs_running:
            parts.append(f"в роботі: {self.jobs_running}")
        if self.mode and self.mode != "ambient":
            parts.append(f"режим {self.mode}")
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
    now: float | None = None,
) -> Situation:
    """Gather the present from what is already being recorded.

    Every source is optional and every failure is absorbed: a situation
    with a hole in it is still worth having, and this runs on a timer
    beside a live conversation.
    """
    now = now if now is not None else time.time()
    hour = time.localtime(now).tm_hour

    bot_state, bot_state_s = "unknown", 0.0
    mode = ""
    if state is not None:
        try:
            bot_state, bot_state_s = _since(state.get("agent_state"), now)
            mode = state.get("mode", "") or ""
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

    running = 0
    if jobs is not None:
        try:
            running = await jobs.running_count()
        except Exception:  # noqa: BLE001
            pass

    recent = [t for t in (unprompted_times or []) if now - t < 3600]

    return Situation(
        now=now,
        hour=hour,
        silence_s=silence_s,
        user_silence_s=user_silence_s,
        bot_state=bot_state,
        bot_state_s=bot_state_s,
        mode=mode,
        jobs_running=running,
        unprompted_last_s=max(0.0, now - unprompted_last_ts) if unprompted_last_ts else 1e9,
        unprompted_1h=len(recent),
    )
