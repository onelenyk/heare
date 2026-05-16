"""Mute gates — file-flag toggles that drop audio frames in the pipeline.

Two independent gates:

* **Output mute** drops ``TTSAudioRawFrame`` after the TTS service so the
  bot speaks silently. Bot text continues to be logged because the
  assistant_response_logger captures upstream of TTS.

* **Input mute** drops ``InputAudioRawFrame`` after the audio transport so
  STT never sees mic audio. The user is "muted to the daemon."

Each gate is keyed off the existence of a flag file, which makes it
trivial to toggle from another process — e.g. the watch dashboard
creates/removes the file when the user presses a hotkey.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Generic flag-file helpers (shared by both gates).
# ---------------------------------------------------------------------------


def _flag_exists(flag_path: Path) -> bool:
    return flag_path.exists()


def _set_flag(flag_path: Path, on: bool) -> bool:
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    if on:
        flag_path.touch(exist_ok=True)
    else:
        try:
            flag_path.unlink()
        except FileNotFoundError:
            pass
    return _flag_exists(flag_path)


def _toggle_flag(flag_path: Path) -> bool:
    return _set_flag(flag_path, not _flag_exists(flag_path))


# ---------------------------------------------------------------------------
# Output (bot) mute API — keep historical names so existing callers/tests
# don't break.
# ---------------------------------------------------------------------------


def is_muted(flag_path: Path) -> bool:
    return _flag_exists(flag_path)


def set_mute(flag_path: Path, muted: bool) -> bool:
    return _set_flag(flag_path, muted)


def toggle_mute(flag_path: Path) -> bool:
    return _toggle_flag(flag_path)


# ---------------------------------------------------------------------------
# Input (mic) mute API — symmetric naming.
# ---------------------------------------------------------------------------


def is_input_muted(flag_path: Path) -> bool:
    return _flag_exists(flag_path)


def set_input_mute(flag_path: Path, muted: bool) -> bool:
    return _set_flag(flag_path, muted)


def toggle_input_mute(flag_path: Path) -> bool:
    return _toggle_flag(flag_path)


# ---------------------------------------------------------------------------
# Pipecat processors.
# ---------------------------------------------------------------------------


_output_gate_cls: type | None = None
_input_gate_cls: type | None = None


def _build_output_gate_class():
    global _output_gate_cls
    if _output_gate_cls is not None:
        return _output_gate_cls

    from pipecat.frames.frames import TTSAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class MuteGateProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(
            self, *, flag_path: Path, session_state: Any = None
        ) -> None:
            super().__init__()
            self._flag_path = flag_path
            # Optional: when the active mode profile has mute_output set
            # (silent / meeting), drop TTS audio the same way the manual
            # output-mute flag does — a hard gate, not a prompt hint.
            self._session_state = session_state

        def _mode_muted(self) -> bool:
            if self._session_state is None:
                return False
            try:
                return bool(self._session_state.profile.mute_output)
            except Exception:
                return False

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSAudioRawFrame) and (
                is_muted(self._flag_path) or self._mode_muted()
            ):
                return
            await self.push_frame(frame, direction)

    _output_gate_cls = MuteGateProcessor
    return _output_gate_cls


def _build_input_gate_class():
    global _input_gate_cls
    if _input_gate_cls is not None:
        return _input_gate_cls

    from pipecat.frames.frames import InputAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class InputMuteGateProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        def __init__(self, *, flag_path: Path) -> None:
            super().__init__()
            self._flag_path = flag_path

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame) and is_input_muted(self._flag_path):
                return
            await self.push_frame(frame, direction)

    _input_gate_cls = InputMuteGateProcessor
    return _input_gate_cls


def create_mute_gate(*, flag_path: Path, session_state: Any = None) -> Any:
    cls = _build_output_gate_class()
    return cls(flag_path=flag_path, session_state=session_state)


def create_input_mute_gate(*, flag_path: Path) -> Any:
    cls = _build_input_gate_class()
    return cls(flag_path=flag_path)


__all__ = [
    "create_input_mute_gate",
    "create_mute_gate",
    "is_input_muted",
    "is_muted",
    "set_input_mute",
    "set_mute",
    "toggle_input_mute",
    "toggle_mute",
]
