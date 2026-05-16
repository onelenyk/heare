"""Mode behavior profiles.

A "mode" used to be a thin flag affecting only turn-finalisation timing
(turn_aggregator) and which indication chimes play (indication/core).
This module promotes it to a data-driven *behavior profile*: each mode
carries its turn timeout, sound policy, system-prompt addendum, tool
deny-list, and proactivity hint. New modes are data here, not new code
branches scattered across the pipeline.

Fail-safe: an unknown / corrupt / misheard mode resolves to AMBIENT (the
historical default) — never to a more-restrictive mode, so a config typo
or a mis-transcribed voice command can never lock the user out of tools.

Layering: this module must not import the pipeline or the indication
subsystem. ``sound_policy`` is expressed as level *value strings*
(IndicationLevel is a str enum, so ``level == "attention"`` holds) and
the indication wire-in adapts it.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass

logger = logging.getLogger("heare.modes")

# Characterised from the pre-change code (no-regression contract):
#   turn_aggregator.py: focus_timeout=0.5, ambient_timeout=3.0,
#     timeout = focus_timeout if mode==FOCUS else ambient_timeout
#   indication/core.py _mode_allows_sound: SILENT→none,
#     FOCUS→{attention,error,input_waiting}, else→all
_FOCUS_TIMEOUT = 0.5
_AMBIENT_TIMEOUT = 3.0
_FOCUS_SOUND_LEVELS = frozenset({"attention", "error", "input_waiting"})

# set_mode must remain callable in every mode — otherwise a restrictive
# mode (meeting) becomes a one-way trip with no voice escape.
_ALWAYS_ALLOWED = frozenset({"set_mode"})


@dataclass(frozen=True)
class ModeProfile:
    """Everything a mode changes about agent behavior.

    ``sound_policy`` is ``None`` to allow every chime, or a frozenset of
    IndicationLevel value strings to allow only those (empty = silent).
    ``denied_tool_patterns`` are fnmatch globs tested against tool names.
    """

    name: str
    prompt_addendum: str
    denied_tool_patterns: tuple[str, ...]
    turn_timeout: float
    sound_policy: frozenset[str] | None
    proactivity: str  # "off" | "low" | "medium" | "high"
    # Hard output gate: when True the pipeline drops the bot's TTS audio
    # entirely (it still hears, thinks, and may run side-effect-free
    # tools — it just does not speak). This is a mechanical mute, not a
    # prompt hint, so "silent" actually means silent.
    mute_output: bool = False


MODE_PROFILES: dict[str, ModeProfile] = {
    "ambient": ModeProfile(
        name="ambient",
        prompt_addendum=(
            "Mode: ambient. Normal conversational assistant — engaged but "
            "not pushy."
        ),
        denied_tool_patterns=(),
        turn_timeout=_AMBIENT_TIMEOUT,
        sound_policy=None,
        proactivity="medium",
    ),
    "focus": ModeProfile(
        name="focus",
        prompt_addendum=(
            "Mode: focus. Be terse and fast. No chit-chat, no follow-up "
            "questions unless essential. Answer and stop."
        ),
        denied_tool_patterns=(),
        turn_timeout=_FOCUS_TIMEOUT,
        sound_policy=_FOCUS_SOUND_LEVELS,
        proactivity="low",
    ),
    "silent": ModeProfile(
        name="silent",
        prompt_addendum=(
            "Mode: silent. Your speech is muted — replies are NOT heard. "
            "Do not converse. Only act when a request needs a side-effect-"
            "free action; otherwise stay idle."
        ),
        denied_tool_patterns=(),
        turn_timeout=_AMBIENT_TIMEOUT,
        sound_policy=frozenset(),
        proactivity="low",
        mute_output=True,
    ),
    "assistant": ModeProfile(
        name="assistant",
        prompt_addendum=(
            "Mode: assistant. Proactive helper — full tool access, may "
            "offer relevant follow-ups and do multi-step work."
        ),
        denied_tool_patterns=(),
        turn_timeout=_AMBIENT_TIMEOUT,
        sound_policy=None,
        proactivity="high",
    ),
    "meeting": ModeProfile(
        name="meeting",
        prompt_addendum=(
            "Mode: meeting. Passive note-taker. Your speech is muted — "
            "replies are NOT heard. Do not converse or act; side-effect "
            "tools are blocked."
        ),
        denied_tool_patterns=(
            "bash",
            "write",
            "edit",
            "stop_daemon",
            "restart_daemon",
            "mcp__*",
            "macos-use*",
        ),
        turn_timeout=_AMBIENT_TIMEOUT,
        sound_policy=frozenset(),
        proactivity="off",
        mute_output=True,
    ),
}

_DEFAULT_MODE = "ambient"

VALID_MODES: tuple[str, ...] = tuple(MODE_PROFILES.keys())


def resolve(name: str | None) -> ModeProfile:
    """Return the profile for *name*; fail safe to AMBIENT on anything odd."""
    key = (name or "").strip().lower()
    profile = MODE_PROFILES.get(key)
    if profile is None:
        logger.warning(
            "modes: unknown mode %r — falling back to %s",
            name,
            _DEFAULT_MODE,
        )
        return MODE_PROFILES[_DEFAULT_MODE]
    return profile


def is_tool_allowed(profile: ModeProfile, tool_name: str) -> bool:
    """True if *tool_name* may run under *profile*.

    ``set_mode`` is unconditionally allowed (escape hatch). Otherwise a
    tool is denied iff its name matches any of the profile's globs.
    """
    if tool_name in _ALWAYS_ALLOWED:
        return True
    for pattern in profile.denied_tool_patterns:
        if fnmatch.fnmatchcase(tool_name, pattern):
            return False
    return True


def mode_gate_refusal(
    session_state: object | None, tool_name: str
) -> dict | None:
    """Shared execution-time gate for the two tool-handler chokepoints.

    Returns a spoken-legible refusal dict (the tool-result shape) and
    logs a ``mode_gate`` line when *tool_name* is denied under the live
    profile; returns ``None`` when the call is allowed (or no session
    state is wired). Callers keep their own action-log bracketing — only
    the allow/deny decision, message, and log are centralised here so
    the built-in and MCP handlers cannot drift apart.
    """
    if session_state is None:
        return None
    profile = session_state.profile  # type: ignore[attr-defined]
    if is_tool_allowed(profile, tool_name):
        return None
    logger.info("mode_gate: blocked %r in %s mode", tool_name, profile.name)
    return {
        "success": False,
        "output": "",
        "error": f"{tool_name} is unavailable in {profile.name} mode",
    }


__all__ = [
    "ModeProfile",
    "MODE_PROFILES",
    "VALID_MODES",
    "resolve",
    "is_tool_allowed",
    "mode_gate_refusal",
]
