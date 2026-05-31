"""CanvasOutputProcessor — persists CanvasContentFrame HTML to the displays table.

Consumes ``CanvasContentFrame`` (a custom frame carrying HTML content for
the live canvas), sanitises it (strips external resource URLs), truncates
at 64 KB, and writes a row to the ``displays`` table via the existing
``log_display`` method.

The processor respects the active mode profile: if ``"canvas"`` is not in
``session_state.profile.outputs`` the frame is passed through untouched.

Pipecat imports are deferred so the module can be imported in tests
without pulling the full stack.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings
    from src.store.storage import TranscriptStore

from src.pipeline.stages.output_router import CanvasContentFrame

logger = logging.getLogger("heare.canvas_output")

MAX_SIZE = 64 * 1024

_processor_cls: type | None = None


def _sanitize(html: str) -> str:
    """Strip external resource URLs from HTML content.

    Removes ``<script src="https://…">``, ``<link href="https://…">``,
    and ``<img src="https://…">`` tags to prevent external resource
    loading in the canvas view.
    """
    html = re.sub(
        r'<script[^>]*src=["\']https?://[^"\']*["\'][^>]*>',
        "", html,
    )
    html = re.sub(
        r'<link[^>]*href=["\']https?://[^"\']*["\'][^>]*>',
        "", html,
    )
    html = re.sub(
        r'<img[^>]*src=["\']https?://[^"\']*["\'][^>]*>',
        "", html,
    )
    return html


def _build_processor_class():
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    from pipecat.processors.frame_processor import FrameProcessor

    class CanvasOutputProcessor(FrameProcessor):  # type: ignore[misc,valid-type]
        """Pipecat FrameProcessor that persists canvas HTML to the displays table.

        Parameters
        ----------
        store
            A ``TranscriptStore`` instance for DB writes.
        settings
            Application settings.
        session_state
            Optional ``SessionState`` for mode-gating the canvas output.
        """

        def __init__(
            self,
            *,
            store: TranscriptStore | None = None,
            settings: Settings | None = None,
            session_state: Any = None,
        ) -> None:
            super().__init__()
            self._store = store
            self._settings = settings
            self._session_state = session_state

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if not isinstance(frame, CanvasContentFrame):
                await self.push_frame(frame, direction)
                return

            # Mode gate — drop canvas output if the active profile
            # does not include "canvas" in its outputs set.
            if self._session_state is not None:
                try:
                    profile = self._session_state.profile
                    if "canvas" not in profile.outputs:
                        logger.info(
                            "Canvas blocked in %s mode",
                            profile.name,
                        )
                        await self.push_frame(frame, direction)
                        return
                except Exception:
                    logger.exception(
                        "Canvas mode-gate check failed (non-fatal); "
                        "allowing frame through"
                    )

            html = _sanitize(frame.text)
            if len(html) > MAX_SIZE:
                html = html[:MAX_SIZE]
                logger.warning("Canvas truncated to %d bytes", MAX_SIZE)

            if self._store is not None:
                try:
                    await self._store.insert_display(
                        content_type="canvas/html", content=html
                    )
                    logger.debug("Canvas HTML persisted (%d bytes)", len(html))
                except Exception:
                    logger.exception("Canvas write failed (non-fatal)")
            else:
                logger.warning("Canvas: no store available, skipping write")

            await self.push_frame(frame, direction)

    _processor_cls = CanvasOutputProcessor
    return _processor_cls


def create_canvas_output(
    *,
    store: TranscriptStore | None = None,
    settings: Settings | None = None,
    session_state: Any = None,
) -> Any:
    """Factory returning a CanvasOutputProcessor instance.

    Insert this processor after the LLM service (or wherever
    ``CanvasContentFrame`` is emitted) so it can persist HTML to the
    ``displays`` table before downstream stages consume or ignore it.
    """
    cls = _build_processor_class()
    return cls(
        store=store,
        settings=settings,
        session_state=session_state,
    )


__all__ = ["create_canvas_output"]
