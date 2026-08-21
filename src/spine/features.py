"""What may be switched off, and what it costs to switch it off.

The conductor already treats every subsystem as optional — an unset
collaborator is a feature that simply does not happen, which is what
made the whole engine testable with fakes. This module is the switch
that was missing: one declaration of what can be turned off, read once
by the composition root.

The point is a bad evening. Something is wrong, the assistant misbehaves
and the cause is not obvious: turn off the suspects one at a time until
it behaves, and the one that fixes it is the one to look at. That is
worth more than any amount of guessing, and it is why `off` here must
mean *not wired at all*, not "wired but told to be quiet".

Two places keep that promise: the composition root (src/spine/main.py)
switches aec, wake, tools, roles, memory, persist and usage; the daemon's
runner (src/daemon/spine_engine.py) switches mcp and telemetry, because
those two are built there. A name in the table that neither one consults
is a switch that lies — tests/test_spine_features.py scans src/ for the
lookup and fails the suite if a name is declared here and read nowhere.

Sources, in order of precedence:
  HEARE_WITHOUT=roles,mcp   env, wins over everything (a bad boot)
  HEARE_SAFE_MODE=1         env, everything optional off at once
  --without roles,mcp       CLI, for `python -m src.spine.main`
  config.toml               spine_features = { roles = false }
  the defaults below
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("spine.features")


@dataclass(frozen=True)
class Feature:
    name: str
    default: bool
    cost: str  # what the user loses, in their language


# Everything here is optional by construction. What is NOT here — audio,
# VAD, the turn clock, STT, the model, TTS — is the engine itself; there
# is no assistant left to debug without it.
FEATURES: tuple[Feature, ...] = (
    Feature("aec", True, "мікрофон глухне, поки асистент говорить (напівдуплекс)"),
    Feature("wake", True, "реагує на будь-яку мову в кімнаті, без імені"),
    Feature("tools", True, "не може нічого робити — тільки розмовляти"),
    Feature("roles", True, "мітинг, вчитель, інтерв'ю, суфлер недоступні"),
    Feature("mcp", True, "зовнішні MCP-сервери не підключаються"),
    Feature("memory", True, "нічого не пам'ятає між розмовами"),
    Feature("persist", True, "розмови не записуються в базу"),
    Feature("usage", True, "витрати не рахуються"),
    Feature("telemetry", True, "немає turns.jsonl — нічим міряти якість"),
    Feature("engine", True, "нічого не тримає між ходами: не озветься сам, "
                            "не пам'ятає, що винен відповідь"),
    # Off by default. Everything it reads is free and nothing is written
    # down, but a thing that watches should be switched on deliberately,
    # not inherited from a default.
    Feature("watcher", False, "не бачить, чим ти зайнятий — двигун знає "
                              "лише про себе"),
    # Also off by default, and for a different reason: what it hears is
    # already transcribed either way, so this changes only what is kept —
    # but the microphone is in a room, and the other people in it did not
    # choose this.
    Feature("hear_all", False, "чує кімнату, але не пам'ятає нічого, що "
                               "сказано не до нього"),
)

_BY_NAME = {f.name: f for f in FEATURES}


def _split(raw: str) -> set[str]:
    return {p.strip().lower() for p in (raw or "").replace(";", ",").split(",") if p.strip()}


def resolve(settings: object, without: str = "") -> dict[str, bool]:
    """Decide what gets wired this run, loudest source first."""
    state = {f.name: f.default for f in FEATURES}

    table = getattr(settings, "spine_features", None)
    if isinstance(table, dict):
        for name, value in table.items():
            if name in state and isinstance(value, bool):
                state[name] = value

    for name in _split(without):
        if name in state:
            state[name] = False
        else:
            logger.warning("--without %r: no such feature (%s)", name, ", ".join(state))

    if os.environ.get("HEARE_SAFE_MODE", "").strip() not in ("", "0", "false"):
        state = {name: False for name in state}
        logger.warning("HEARE_SAFE_MODE: every optional subsystem is off")

    for name in _split(os.environ.get("HEARE_WITHOUT", "")):
        if name in state:
            state[name] = False

    return state


def describe(state: dict[str, bool]) -> str:
    """One line for the log: what is on, what is off."""
    return " ".join(
        f"{name}={'on' if enabled else 'OFF'}" for name, enabled in state.items()
    )


def losses(state: dict[str, bool]) -> list[str]:
    """Plain-language consequences of what is switched off, for the log
    and the dashboard: a feature silently missing is worse than a bug."""
    return [
        f"{f.name}: {f.cost}" for f in FEATURES if not state.get(f.name, f.default)
    ]


__all__ = ["FEATURES", "Feature", "resolve", "describe", "losses"]
