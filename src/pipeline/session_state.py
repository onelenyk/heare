"""Shared per-session state: language (composed) + active mode profile.

``LanguageState`` already is the single source of truth for the active
user language and is consumed by an existing listener that rebuilds the
system prompt. We deliberately **compose** it here rather than absorb it:
SessionState holds a reference to the existing LanguageState instance
(unmodified) and adds the live ``ModeProfile`` with its own, separate
mode-change listener. Merging the two listeners would multiplex two
orthogonal signals through one slot and risk regressing the
well-tested language-switch path — composition avoids that entirely.

A ``flush_pending`` hook lets the ``set_mode`` tool force-submit any
buffered partial utterance in the turn aggregator *before* the mode
flips, so speech spoken mid-switch is not silently dropped.

No Pipecat imports — admin CLI paths import this without portaudio.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from src.agent.modes import ModeProfile, resolve
from src.pipeline.language_state import LanguageState

logger = logging.getLogger("heare.session_state")


class SessionState:
    """Single source of truth for the active mode (and a handle on
    the shared :class:`LanguageState`)."""

    def __init__(
        self,
        language_state: LanguageState,
        *,
        state=None,
        initial_mode: str = "ambient",
    ) -> None:
        self._language_state = language_state
        if state is not None:
            mode = state.get("mode", initial_mode)
            if mode:
                initial_mode = mode
        self._profile: ModeProfile = resolve(initial_mode)
        self._mode_listener: Optional[Callable[[ModeProfile], None]] = None
        self._flush_hook: Optional[Callable[[], None]] = None

    # --- language (composed, read-through) ---------------------------------

    @property
    def language_state(self) -> LanguageState:
        return self._language_state

    @property
    def language(self) -> str:
        return self._language_state.language

    # --- mode --------------------------------------------------------------

    @property
    def profile(self) -> ModeProfile:
        return self._profile

    @property
    def mode(self) -> str:
        return self._profile.name

    def set_mode(self, name: str) -> bool:
        """Resolve *name* (fail-safe to ambient) and set it live.

        Returns True if the effective profile changed. Fires the
        mode-change listener only on an actual change.
        """
        new_profile = resolve(name)
        if new_profile.name == self._profile.name:
            return False
        old = self._profile.name
        self._profile = new_profile
        logger.info("[SESSION STATE] mode %s -> %s", old, new_profile.name)
        if self._mode_listener is not None:
            try:
                self._mode_listener(new_profile)
            except Exception:
                logger.exception(
                    "session_state: mode listener raised (non-fatal)"
                )
        return True

    def set_mode_change_listener(
        self, listener: Optional[Callable[[ModeProfile], None]]
    ) -> None:
        """Register a callback fired when the mode actually changes.

        Separate from LanguageState's listener — the language path is
        untouched. Only one listener; re-registering replaces it.
        """
        self._mode_listener = listener

    # --- turn-buffer flush hook -------------------------------------------

    def register_flush_hook(
        self, hook: Optional[Callable[[], None]]
    ) -> None:
        """Let the turn aggregator register a force-submit callback so
        ``set_mode`` can flush a half-spoken turn before switching."""
        self._flush_hook = hook

    def flush_pending(self) -> None:
        """Invoke the registered flush hook, if any. Best-effort."""
        if self._flush_hook is None:
            return
        try:
            self._flush_hook()
        except Exception:
            logger.exception(
                "session_state: flush hook raised (non-fatal)"
            )


# Module-level handle so the `set_mode` direct tool (which only receives
# args + settings, not pipeline objects) can reach the live SessionState.
# Mirrors the browser-bridge accessor pattern. build_pipeline sets this
# once after construction.
_active: "SessionState | None" = None


def set_active_session_state(state: "SessionState | None") -> None:
    global _active
    _active = state


def get_active_session_state() -> "SessionState | None":
    return _active


__all__ = [
    "SessionState",
    "set_active_session_state",
    "get_active_session_state",
]
