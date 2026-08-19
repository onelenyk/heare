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

import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger("heare.spine.role_session")


class RoleLike(Protocol):
    """The minimal shape of a Role this module depends on."""

    name: str
    channel: str  # "voice" | "log"
    artifact: str  # instruction for building the end-of-session artifact
    prompt: str


_FALLBACK_SPOKEN = "Готово, підсумок збережено."
_FAILURE_SPOKEN = "Не вдалося зібрати підсумок, але запис збережено."
_SPOKEN_MARKER = "===SPOKEN==="


NOTHING_HEARD_LINE = "Нічого не записалось — підсумок не збираю."


@dataclass
class Artifact:
    full_md: str  # the complete artifact, markdown — saved to a file by the caller
    spoken: str  # 2-4 sentences to say aloud, plain prose, Ukrainian


class RoleManager:
    """Owns the one active role session (at most one at a time)."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        persist: Any | None = None,
    ) -> None:
        self._clock = clock
        self._active: Any | None = None
        self._session_turns: list[int] = []
        self._started_at: float | None = None
        # Optional collaborator: SpinePersistence, or anything with the
        # same four methods. None means exactly the behaviour this class
        # had before persistence existed — the session lives in memory
        # only. Public so the conductor can wire it after construction
        # (main.py builds the RoleManager before the DB handle exists).
        self.persist = persist
        self._session_id: int | None = None
        # Survives _reset so the caller can attach the artifact path it
        # only learns after finish() has already closed the row.
        self._last_session_id: int | None = None

    @property
    def active(self) -> Any | None:
        return self._active

    @property
    def session_id(self) -> int | None:
        """The DB id of the live session (None without persistence)."""
        return self._session_id

    @property
    def last_session_id(self) -> int | None:
        """The DB id of the most recently closed session."""
        return self._last_session_id

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
        self._session_id = None
        if self.persist is not None:
            try:
                self._session_id = self.persist.open_role_session(
                    role.name, getattr(role, "channel", "") or ""
                )
                self._last_session_id = self._session_id
            except Exception:
                # A DB that will not take the row must not stop a meeting
                # from being held; the in-memory session still works.
                logger.exception("role session not persisted (non-fatal)")
        if role.channel == "log":
            return f'Роль «{role.name}» активна. Записую все.'
        return f'Роль «{role.name}» активна.'

    def note_turn(self, turn_id: int | None) -> None:
        if turn_id is None:
            return
        self._session_turns.append(turn_id)
        if self.persist is not None and self._session_id is not None:
            try:
                self.persist.note_role_turn(self._session_id, turn_id)
            except Exception:
                logger.exception("role turn not persisted (non-fatal)")

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
            self._close_persisted("done")
            self._reset()
            return None

        transcript = _render_transcript(exchanges)

        # A model asked to summarise an empty meeting writes one. A real
        # session on this machine — fifteen seconds, zero turns — produced
        # a protocol naming a Tech Lead, an $800 freelancer and a release
        # date, none of which existed. A record that invents its content
        # is worse than no record, because it is believed later. Nothing
        # heard, nothing summarised.
        if not transcript.strip():
            logger.info("role %r: nothing was said — no artifact", role.name)
            self._close_persisted("empty")
            self._reset()
            return Artifact(full_md="", spoken=NOTHING_HEARD_LINE)

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
            # The session ended the way the user asked; only the summary
            # failed, and the transcript is the artifact — 'done'.
            self._close_persisted("done")
            self._reset()
            return Artifact(full_md=transcript, spoken=_FAILURE_SPOKEN)

        self._close_persisted("done")
        self._reset()
        return _parse_response(response)

    def cancel(self) -> str | None:
        """Drop the active session without producing an artifact."""
        role = self._active
        if role is None:
            return None
        # Ended, but not by «закінчили» and with nothing built: from the
        # DB's point of view this session was cut off, same as a restart.
        self._close_persisted("interrupted")
        self._reset()
        return f'Роль «{role.name}» скасована.'

    def note_artifact(self, artifact_path: str) -> None:
        """Attach the saved artifact file to the session just closed.

        The path exists only after the caller has written the file, which
        is necessarily after finish() returned.
        """
        session_id = self._last_session_id
        if self.persist is None or session_id is None or not artifact_path:
            return
        try:
            self.persist.set_role_session_artifact(session_id, artifact_path)
        except Exception:
            logger.exception("artifact path not persisted (non-fatal)")

    def _close_persisted(self, status: str, artifact_path: str = "") -> None:
        session_id = self._session_id
        self._session_id = None
        if self.persist is None or session_id is None:
            return
        try:
            self.persist.close_role_session(session_id, artifact_path, status)
        except Exception:
            logger.exception("role session not closed in DB (non-fatal)")

    def _reset(self) -> None:
        self._active = None
        self._session_id = None
        self._session_turns = []
        self._started_at = None


def _render_transcript(exchanges: list[dict]) -> str:
    lines: list[str] = []
    for exchange in exchanges:
        # A blank line is not something anyone said, and a transcript of
        # blanks is what makes a model invent a meeting.
        user = (exchange.get("user") or "").strip()
        if user:
            lines.append(f"Користувач: {user}")
        agent = (exchange.get("agent") or "").strip()
        if agent:
            lines.append(f"Асистент: {agent}")
    return "\n".join(lines)


def _parse_response(response: str) -> Artifact:
    if _SPOKEN_MARKER not in response:
        return Artifact(full_md=response, spoken=_FALLBACK_SPOKEN)
    full_md, _, spoken = response.partition(_SPOKEN_MARKER)
    return Artifact(full_md=full_md.strip(), spoken=spoken.strip())
