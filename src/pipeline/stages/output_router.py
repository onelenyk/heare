"""OutputRouter — parse tagged LLM output and route to typed content frames.

Parses ``[voice]...[/voice]``, ``[text]...[/text]``,
``[canvas]...[/canvas]`` from streaming ``LLMTextFrame`` chunks. Emits
typed frames (:class:`VoiceContentFrame`, :class:`TextContentFrame`,
:class:`CanvasContentFrame`) for downstream processors.

Streaming-aware: partial tags stay in buffer until closing delimiter
arrives. Untagged text is emitted as :class:`TextContentFrame` with a
warning. Nested tags follow "inner wins" semantics — the innermost
active tag determines the output frame type.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pipecat.frames.frames import Frame

logger = logging.getLogger("heare.output_router")


# ---------------------------------------------------------------------------
# Custom frame types — Frame subclasses for Pipecat pipeline routing.
# ---------------------------------------------------------------------------


@dataclass
class VoiceContentFrame(Frame):  # type: ignore[misc]
    text: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()


@dataclass
class TextContentFrame(Frame):  # type: ignore[misc]
    text: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()


@dataclass
class CanvasContentFrame(Frame):  # type: ignore[misc]
    text: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KNOWN_TAGS: tuple[str, ...] = ("voice", "text", "canvas")

# ---------------------------------------------------------------------------
# Pipecat-bound class — lives in a closure so the import is deferred.
# ---------------------------------------------------------------------------

_processor_cls: type | None = None


def _build_processor_class() -> type:
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    from pipecat.frames.frames import LLMFullResponseEndFrame, LLMTextFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class OutputRouter(FrameProcessor):  # type: ignore[misc,valid-type]

        def __init__(self) -> None:
            super().__init__()
            self._buffer = ""
            self._current_tag: str | None = None
            self._tag_stack: list[str] = []

        async def process_frame(self, frame, direction) -> None:  # type: ignore[override]
            await super().process_frame(frame, direction)

            if isinstance(frame, LLMTextFrame):
                self._buffer += frame.text
                await self._parse_buffer()

            elif isinstance(frame, LLMFullResponseEndFrame):
                await self._flush_buffer()
                await self.push_frame(frame, direction)

            else:
                await self.push_frame(frame, direction)

        async def _parse_buffer(self) -> None:
            while True:
                if self._current_tag is not None:
                    end_tag = f"[/{self._current_tag}]"
                    end_idx = self._buffer.find(end_tag)

                    nested_tag: str | None = None
                    nested_idx: int = -1
                    for tag in _KNOWN_TAGS:
                        open_tag = f"[{tag}]"
                        idx2 = self._buffer.find(open_tag)
                        if idx2 >= 0 and (nested_idx < 0 or idx2 < nested_idx):
                            nested_tag = tag
                            nested_idx = idx2

                    if nested_idx >= 0 and (end_idx < 0 or nested_idx < end_idx):
                        content = self._buffer[:nested_idx].strip()
                        self._buffer = self._buffer[
                            nested_idx + len(f"[{nested_tag}]") :
                        ]
                        if content:
                            await self._emit_frame(self._current_tag, content)
                        self._tag_stack.append(self._current_tag)
                        self._current_tag = nested_tag
                        continue

                    if end_idx >= 0:
                        content = self._buffer[:end_idx].strip()
                        self._buffer = self._buffer[end_idx + len(end_tag) :]
                        if content:
                            await self._emit_frame(self._current_tag, content)
                        if self._tag_stack:
                            self._current_tag = self._tag_stack.pop()
                        else:
                            self._current_tag = None
                        continue

                    break

                earliest_tag: str | None = None
                earliest_idx: int = -1
                for tag in _KNOWN_TAGS:
                    open_tag = f"[{tag}]"
                    idx = self._buffer.find(open_tag)
                    if idx >= 0 and (earliest_idx < 0 or idx < earliest_idx):
                        earliest_tag = tag
                        earliest_idx = idx

                if earliest_tag is not None:
                    before = self._buffer[:earliest_idx].strip()
                    self._buffer = self._buffer[
                        earliest_idx + len(f"[{earliest_tag}]") :
                    ]
                    if before:
                        logger.warning(
                            "Untagged text before [%s]: %r",
                            earliest_tag,
                            before[:80],
                        )
                        await self._emit_frame("text", before)
                    self._current_tag = earliest_tag
                    continue

                break

        async def _flush_buffer(self) -> None:
            if not self._buffer.strip():
                self._buffer = ""
                self._current_tag = None
                return

            if self._current_tag is not None or self._tag_stack:
                logger.warning(
                    "Unclosed tag(s) at end of response: current=%s stack=%s",
                    self._current_tag,
                    self._tag_stack,
                )
            logger.warning("Untagged text at end: %r", self._buffer[:80])
            await self._emit_frame("text", self._buffer.strip())
            self._buffer = ""
            self._current_tag = None
            self._tag_stack.clear()

        async def _emit_frame(self, tag: str, content: str) -> None:
            if not content:
                return
            if tag == "voice":
                await self.push_frame(VoiceContentFrame(text=content))
            elif tag == "text":
                await self.push_frame(TextContentFrame(text=content))
            elif tag == "canvas":
                await self.push_frame(CanvasContentFrame(text=content))

    _processor_cls = OutputRouter
    return _processor_cls


def _get_output_router_class() -> type:
    return _build_processor_class()


def create_output_router() -> object:
    cls = _build_processor_class()
    return cls()


def __getattr__(name: str) -> object:
    if name == "OutputRouter":
        return _get_output_router_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CanvasContentFrame",
    "OutputRouter",
    "TextContentFrame",
    "VoiceContentFrame",
    "create_output_router",
]
