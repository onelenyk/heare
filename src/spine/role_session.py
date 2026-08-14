"""A Role runs as one bounded session: start on voice trigger, end on
an end-phrase, produce an Artifact.

`RoleManager` owns at most one active role session at a time. While a
role is active it collects the persisted turn ids of the exchanges that
happened during the session; on finish() it renders those exchanges as
a transcript and asks the caller's one-shot LLM `complete` to build a
markdown artifact plus a short spoken summary in one request.

No pipecat, no SDK — just stdlib. `Role` itself is not imported here:
the conductor wires real Role instances (from src/spine/roles.py, which
may not exist yet) in later. This module only needs the shape below,
expressed as a Protocol so any object with these attributes works.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


class RoleLike(Protocol):
    """The minimal shape of a Role this module depends on."""

    name: str
    channel: str  # "voice" | "log"
    artifact: str  # instruction for building the end-of-session artifact
    prompt: str


_FALLBACK_SPOKEN = "Готово, підсумок збережено."
_FAILURE_SPOKEN = "Не вдалося зібрати підсумок, але запис збережено."
_SPOKEN_MARKER = "===SPOKEN==="


@dataclass
class Artifact:
    full_md: str  # the complete artifact, markdown — saved to a file by the caller
    spoken: str  # 2-4 sentences to say aloud, plain prose, Ukrainian


class RoleManager:
    """Owns the one active role session (at most one at a time)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._active: Any | None = None
        self._session_turns: list[int] = []
        self._started_at: float | None = None

    @property
    def active(self) -> Any | None:
        return self._active

    @property
    def session_turns(self) -> list[int]:
        return self._session_turns

    def minutes(self) -> int:
        """Elapsed minutes of the current session (0 when none active)."""
        if self._started_at is None:
            return 0
        return int((self._clock() - self._started_at) // 60)

    def start(self, role: Any) -> str:
        """Activate. Returns a short Ukrainian spoken ack derived from the
        role name. Starting while another role is active returns a
        refusal string mentioning the active role and does NOT switch.
        """
        if self._active is not None:
            return (
                f'Роль «{self._active.name}» вже активна. '
                "Спершу завершіть її."
            )
        self._active = role
        self._session_turns = []
        self._started_at = self._clock()
        if role.channel == "log":
            return f'Роль «{role.name}» активна. Записую все.'
        return f'Роль «{role.name}» активна.'

    def note_turn(self, turn_id: int | None) -> None:
        if turn_id is None:
            return
        self._session_turns.append(turn_id)

    async def finish(
        self,
        *,
        exchanges: list[dict],
        complete: Callable[[list[dict]], Awaitable[str]],
    ) -> Artifact | None:
        """Deactivate. None when no role was active or role.artifact is
        empty. Otherwise builds one LLM request and returns the parsed
        Artifact. A failed LLM call still closes the session and never
        loses the transcript.
        """
        role = self._active
        if role is None:
            return None

        if not role.artifact:
            self._reset()
            return None

        transcript = _render_transcript(exchanges)

        try:
            system = (
                f"{role.artifact}\n\n"
                "Формат відповіді — суворо: спершу markdown-артефакт "
                f"повністю, потім рядок '{_SPOKEN_MARKER}', потім 2-4 "
                "речення для озвучення вголос українською, простою "
                "прозою."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": transcript},
            ]
            response = await complete(messages)
        except Exception:
            self._reset()
            return Artifact(full_md=transcript, spoken=_FAILURE_SPOKEN)

        self._reset()
        return _parse_response(response)

    def cancel(self) -> str | None:
        """Drop the active session without producing an artifact."""
        role = self._active
        if role is None:
            return None
        self._reset()
        return f'Роль «{role.name}» скасована.'

    def _reset(self) -> None:
        self._active = None
        self._session_turns = []
        self._started_at = None


def _render_transcript(exchanges: list[dict]) -> str:
    lines: list[str] = []
    for exchange in exchanges:
        user = exchange.get("user")
        if user:
            lines.append(f"Користувач: {user}")
        agent = exchange.get("agent")
        if agent is not None:
            lines.append(f"Асистент: {agent}")
    return "\n".join(lines)


def _parse_response(response: str) -> Artifact:
    if _SPOKEN_MARKER not in response:
        return Artifact(full_md=response, spoken=_FALLBACK_SPOKEN)
    full_md, _, spoken = response.partition(_SPOKEN_MARKER)
    return Artifact(full_md=full_md.strip(), spoken=spoken.strip())
