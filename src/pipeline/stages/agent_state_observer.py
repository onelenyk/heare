"""Agent-state observer: tracks the bot's speaking state via Pipecat frames.

Writes the current agent state (idle / talking / interrupted) to the State
store so the watch dashboard can render an indicator.  Driven entirely by
Pipecat system frames — no timers, no auto-decay.

State machine::

    BotStartedSpeakingFrame  → "talking"
    BotStoppedSpeakingFrame  → "idle"
    InterruptionFrame        → "interrupted"

The consumer (watch dashboard) decides when to transition out of
"interrupted" — the observer only writes what it sees.
"""
from __future__ import annotations

import json
import logging
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


logger = logging.getLogger("heare.agent_state")


class AgentStateObserver(FrameProcessor):
    """Pipeline pass-through that mirrors bot speaking state into the State store.

    Forwards every frame unchanged; the only side effect is the
    ``state.set("agent_state", ...)`` call on the three transition frames
    listed in the module docstring.
    """

    def __init__(self, state) -> None:
        super().__init__()
        self._state = state

    async def process_frame(self, frame, direction: FrameDirection) -> None:  # type: ignore[override]
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            await self._state.set("agent_state", json.dumps({
                "state": "talking",
                "since_ts": time.time(),
            }))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._state.set("agent_state", json.dumps({
                "state": "idle",
                "since_ts": time.time(),
            }))
        elif isinstance(frame, InterruptionFrame):
            await self._state.set("agent_state", json.dumps({
                "state": "interrupted",
                "since_ts": time.time(),
            }))
        await self.push_frame(frame, direction)


def create_agent_state_observer(state) -> AgentStateObserver:
    return AgentStateObserver(state)


__all__ = [
    "AgentStateObserver",
    "create_agent_state_observer",
]
