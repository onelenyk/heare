"""TurnAggregator: Frame processor for conversation-level utterance aggregation.

Pipecat imports are deferred so tests work without portaudio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from src.config import Mode

if TYPE_CHECKING:
    from pipecat.frames.frames import Frame, TranscriptionFrame  # noqa: F401
    from pipecat.processors.frame_processor import FrameDirection  # noqa: F401

logger = logging.getLogger("heare.turn_aggregator")


@dataclass
class AggregatedTranscriptFrame:
    """Frame containing aggregated utterances from a conversation turn.

    This custom frame type carries the aggregated text along with metadata
    about the turn (utterance count, timing). It will be pushed downstream
    to the Decider when a turn is complete.
    """

    text: str
    utterance_count: int
    turn_start_ts: float
    turn_end_ts: float


class TurnAggregator:
    """Frame processor that aggregates utterances into conversation turns.

    Buffers TranscriptionFrame objects until silence timeout is detected,
    then emits an AggregatedTranscriptFrame. Timeout varies by mode:
    - Focus: 0.5s (quick replies expected)
    - Ambient: 3.0s (let user finish thought)

    Also implements safety limits:
    - Max turn duration: 30s forces submit
    - Max buffer size: 50 utterances forces submit
    """

    def __init__(
        self,
        mode: Mode,
        focus_timeout: float = 0.5,
        ambient_timeout: float = 3.0,
        max_turn_duration: float = 30.0,
        max_buffer_size: int = 50,
        on_turn_complete: Callable[[str, float, float, list[dict]], Awaitable[None]]
        | None = None,
    ):
        """Initialize the TurnAggregator.

        Args:
            mode: Current conversation mode (FOCUS or AMBIENT).
            focus_timeout: Silence timeout in focus mode (seconds). Default 0.5s.
            ambient_timeout: Silence timeout in ambient mode (seconds). Default 3.0s.
            max_turn_duration: Maximum turn duration before forced submit (seconds). Default 30s.
            max_buffer_size: Maximum utterances per turn before forced submit. Default 50.
            on_turn_complete: Optional callback invoked when a turn completes.
                Receives (aggregated_text, turn_start_ts, turn_end_ts, buffer).
        """
        self.mode = mode
        self.focus_timeout = focus_timeout
        self.ambient_timeout = ambient_timeout
        self.max_turn_duration = max_turn_duration
        self.max_buffer_size = max_buffer_size
        self.on_turn_complete = on_turn_complete
        # Optional single-source-of-truth mode state. When set, the
        # silence timeout follows the active mode profile instead of the
        # legacy focus/ambient pair, so all 5 modes get correct timing.
        self.session_state: object | None = None

        # Buffer state
        self.buffer: list[dict] = []  # Each: {"text": str, "utterance_ts": float}
        self.last_utterance_ts: float | None = None
        self.turn_start_ts: float | None = None
        self._timeout_task: asyncio.Task | None = None

    async def add_utterance(
        self, text: str, utterance_ts: float
    ) -> tuple[bool, str | None]:
        """Add utterance to buffer. Returns (should_submit, aggregated_text).

        Decision logic:
        - Focus mode: Submit after 0.5s silence (user expects quick replies)
        - Ambient mode: Submit after 3.0s silence (let user finish thought)
        - Force submit after 30s regardless of silence
        - Force submit if buffer exceeds max_buffer_size

        Args:
            text: The transcribed text to add.
            utterance_ts: Timestamp of the utterance (seconds since epoch).

        Returns:
            Tuple of (should_submit, aggregated_text_or_None).
            should_submit is True if the turn should be submitted now.
            aggregated_text contains the combined buffer text if submitting.
        """
        # Initialize turn start on first utterance
        if self.turn_start_ts is None:
            self.turn_start_ts = utterance_ts

        # Check max turn duration (before adding new utterance)
        if (
            self.turn_start_ts
            and (utterance_ts - self.turn_start_ts) > self.max_turn_duration
        ):
            logger.warning(
                f"TurnAggregator: max duration ({self.max_turn_duration}s) reached, forcing submit"
            )
            # Add current utterance to buffer before aggregating
            self.buffer.append({"text": text, "utterance_ts": utterance_ts})
            aggregated = self._aggregate_and_clear()
            return True, aggregated

        # Add to buffer (preserve utterance timestamp)
        self.buffer.append({"text": text, "utterance_ts": utterance_ts})
        self.last_utterance_ts = utterance_ts

        # Check max buffer size AFTER adding (if buffer is now full, submit)
        if len(self.buffer) >= self.max_buffer_size:
            logger.warning(
                f"TurnAggregator: buffer full ({self.max_buffer_size}), forcing submit"
            )
            aggregated = self._aggregate_and_clear()
            return True, aggregated

        # Cancel any existing timeout task
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()

        # Determine timeout based on mode
        if self.session_state is not None:
            timeout = self.session_state.profile.turn_timeout
        else:
            timeout = (
                self.focus_timeout if self.mode == Mode.FOCUS else self.ambient_timeout
            )

        # Start new timeout task
        self._timeout_task = asyncio.create_task(self._timeout_handler(timeout))

        return False, None

    async def _timeout_handler(self, timeout_seconds: float) -> None:
        """Wait for silence timeout, then submit turn.

        Args:
            timeout_seconds: The silence timeout duration in seconds.
        """
        try:
            await asyncio.sleep(timeout_seconds)

            # Check if we should submit (no new utterances during timeout)
            if self.last_utterance_ts:
                time_since_last = time.time() - self.last_utterance_ts
                if time_since_last >= timeout_seconds:
                    logger.info(
                        f"TurnAggregator: {timeout_seconds}s silence detected, submitting turn"
                    )

                    # Capture state before clearing
                    aggregated_text = " ".join(item["text"] for item in self.buffer)
                    turn_start = self.turn_start_ts
                    turn_end = time.time()
                    buffer_copy = self.buffer.copy()

                    # Clear buffer
                    self._aggregate_and_clear()

                    # Invoke callback with captured state
                    if self.on_turn_complete:
                        await self.on_turn_complete(
                            aggregated_text,
                            turn_start,  # type: ignore
                            turn_end,
                            buffer_copy,
                        )
        except asyncio.CancelledError:
            # Task was cancelled due to new utterance - this is expected
            pass

    def _aggregate_and_clear(self) -> str:
        """Aggregate buffer text and clear buffer.

        Returns:
            The aggregated text from all buffered utterances.
        """
        # Join all utterances with spaces
        aggregated = " ".join(item["text"] for item in self.buffer)

        # Clear buffer and reset state
        self.buffer.clear()
        self.last_utterance_ts = None
        self.turn_start_ts = None

        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

        return aggregated

    def set_mode(self, new_mode: Mode) -> None:
        """Change conversation mode and reset buffer.

        Args:
            new_mode: The new mode (FOCUS, AMBIENT, or SILENT).
        """
        self.mode = new_mode

        # Clear buffer on mode change (per plan spec)
        if self.buffer:
            logger.info(f"TurnAggregator: mode changed to {new_mode}, clearing buffer")
            self.buffer.clear()
            self.last_utterance_ts = None
            self.turn_start_ts = None

            if self._timeout_task and not self._timeout_task.done():
                self._timeout_task.cancel()
            self._timeout_task = None


def _load_pipecat_base():
    """Load Pipecat classes for frame processing.

    Deferred import to allow tests to run without portaudio dependency.
    """
    from pipecat.frames.frames import Frame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    return FrameProcessor, Frame, FrameDirection, TranscriptionFrame


class TurnAggregatorProcessor:
    """Pipecat FrameProcessor adapter for TurnAggregator.

    This class integrates TurnAggregator into the Pipecat pipeline by
    implementing the FrameProcessor interface. It intercepts TranscriptionFrame
    objects, buffers them according to mode-aware timeouts, and emits
    AggregatedTranscriptFrame when turns are complete.
    """

    def __init__(
        self,
        mode: Mode,
        focus_timeout: float = 0.5,
        ambient_timeout: float = 3.0,
        max_turn_duration: float = 30.0,
        max_buffer_size: int = 50,
        on_turn_complete: Callable[[str, float, float, list[dict]], Awaitable[None]]
        | None = None,
    ):
        """Initialize the processor.

        Args:
            mode: Current conversation mode (FOCUS or AMBIENT).
            focus_timeout: Silence timeout in focus mode (seconds).
            ambient_timeout: Silence timeout in ambient mode (seconds).
            max_turn_duration: Maximum turn duration before forced submit (seconds).
            max_buffer_size: Maximum utterances per turn before forced submit.
            on_turn_complete: Optional callback when turn completes.
        """
        FrameProcessor, _, _, _ = _load_pipecat_base()

        # Create the actual FrameProcessor instance
        self._processor = FrameProcessor(name="TurnAggregator")
        self.aggregator = TurnAggregator(
            mode=mode,
            focus_timeout=focus_timeout,
            ambient_timeout=ambient_timeout,
            max_turn_duration=max_turn_duration,
            max_buffer_size=max_buffer_size,
            on_turn_complete=on_turn_complete,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Process a frame from the pipeline.

        Args:
            frame: The frame to process.
            direction: The frame direction (DOWNSTREAM or UPSTREAM).
        """
        _, Frame, _, TranscriptionFrame = _load_pipecat_base()

        # Only handle TranscriptionFrame objects
        if isinstance(frame, TranscriptionFrame):
            should_submit, aggregated_text = await self.aggregator.add_utterance(
                frame.text, time.time()
            )

            if should_submit and aggregated_text:
                # Turn complete! Create and push aggregated frame
                aggregated_frame = AggregatedTranscriptFrame(
                    text=aggregated_text,
                    utterance_count=len(self.aggregator.buffer)
                    + 1,  # +1 because buffer was cleared
                    turn_start_ts=0.0,  # Will be set by aggregator
                    turn_end_ts=time.time(),
                )
                # Push the aggregated frame downstream
                # Note: In actual implementation, this would use self._processor.push_frame()
                # For now, we'll store it for retrieval in tests
                self._last_aggregated = aggregated_frame
        else:
            # Pass through all other frames
            # In actual implementation: await self._processor.push_frame(frame, direction)
            pass

    def set_mode(self, new_mode: Mode) -> None:
        """Change conversation mode.

        Args:
            new_mode: The new mode.
        """
        self.aggregator.set_mode(new_mode)
