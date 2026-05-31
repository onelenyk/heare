"""TextOutputProcessor — log text-only content to the transcripts table.

Consumes ``TextContentFrame`` emitted by the ``OutputRouter`` when the
LLM's response includes a ``[text]`` tag (or the entire response is
untagged, which defaults to ``TextContentFrame``).

Writes to the ``transcripts`` table via ``TranscriptStore`` with
``agent_spoken=False`` so the watch dashboard and context builder see
the assistant's text output without confusing it with spoken TTS output.

This processor is NEVER gated by mute or mode — text always flows.

Pipecat imports are deferred so the module can be imported in tests
without pulling the full stack.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings
    from src.store.storage import TranscriptStore


logger = logging.getLogger("heare.text_output")


def _load_pipecat_base():
    from pipecat.frames.frames import Frame
    from pipecat.processors.frame_processor import FrameProcessor

    return (
        FrameProcessor,
        Frame,
    )


def _load_text_content_frame():
    """Lazy-load TextContentFrame from the stage that defines it.

    The OutputRouter (src.pipeline.stages.output_router) emits typed
    content frames; this processor consumes TextContentFrame.  The
    deferred import avoids a hard dependency between parallel tasks.
    """
    from src.pipeline.stages.output_router import TextContentFrame

    return TextContentFrame


_processor_cls: type | None = None


def _build_processor_class():
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    FrameProcessor, Frame = _load_pipecat_base()
    TextContentFrame = _load_text_content_frame()

    class TextOutputProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Persist the LLM's text-only output to the transcripts table.

        Every ``TextContentFrame`` that passes through is logged with
        ``agent_spoken=False`` so consumers can distinguish written
        assistant output from speech.

        All frames are passed through unchanged — this processor is
        read-only with respect to the pipeline.
        """

        def __init__(
            self,
            *,
            store: "TranscriptStore | None" = None,
            settings: "Settings | None" = None,
        ) -> None:
            super().__init__()
            self._store = store
            self._settings = settings

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, TextContentFrame):
                text = getattr(frame, "text", "") or ""
                if text and self._store is not None:
                    try:
                        await self._store.log_transcript(
                            text=text,
                            mode="assistant",
                            agent_spoken=False,
                        )
                        logger.debug("logged text output: %s", text[:60])
                    except Exception:
                        logger.exception("failed to log text output")

            await self.push_frame(frame, direction)

    _processor_cls = TextOutputProcessor
    return _processor_cls


def create_text_output(
    *,
    store: "TranscriptStore | None" = None,
    settings: "Settings | None" = None,
) -> Any:
    """Factory returning a TextOutputProcessor instance.

    Place AFTER the OutputRouter in the pipeline so it sees typed
    content frames rather than raw tagged text.

    This processor is read-only — it logs text to the store and
    passes every frame through unchanged.
    """
    cls = _build_processor_class()
    return cls(
        store=store,
        settings=settings,
    )


__all__ = ["create_text_output"]
