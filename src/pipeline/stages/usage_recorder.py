"""Usage recorder — captures pipecat metrics events and TranscriptionFrame
markers, writing them to ``usage_events`` so the dashboard can show
running call counts and dollar cost across LLM, STT, and TTS.

Three event sources:

* ``MetricsFrame`` containing ``LLMUsageMetricsData`` — token counts +
  model name from the LLM service. Cost computed via :mod:`src.agent.llm.pricing`.
* ``MetricsFrame`` containing ``TTSUsageMetricsData`` — character count
  from the TTS service. Recorded even when the cost is $0 (e.g.
  ``edge_tts``) so the user can see call volume in statistics.
* ``TranscriptionFrame`` (finalized) — one event per finished STT call.
  Audio duration is bracketed by ``UserStartedSpeakingFrame`` →
  ``UserStoppedSpeakingFrame`` so the recorder can attach a real
  ``audio_seconds`` value to the row.

Placement: after ``tts`` in the pipeline so the recorder observes
metrics emitted by every upstream service plus the speech-bracket
frames. Observe-only: every frame is pushed downstream unchanged.

DB writes are fire-and-forget via ``asyncio.create_task`` so the audio
loop never blocks on disk. Failures are logged but don't propagate.
Pipecat imports are deferred so admin CLI paths import this module
without portaudio.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.agent.llm.pricing import llm_cost, stt_cost, tts_cost


logger = logging.getLogger("heare.usage_recorder")


_processor_cls: type | None = None


def _build_processor_class():
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    from pipecat.frames.frames import (
        MetricsFrame,
        TranscriptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
    from pipecat.processors.frame_processor import FrameProcessor

    class UsageRecorder(FrameProcessor):  # type: ignore[misc,valid-type]
        """Persist LLM/STT/TTS events to the transcript store.

        Parameters
        ----------
        store
            A ``TranscriptStore`` instance.
        provider_getter
            No-arg callable returning the active LLM provider key. Read
            at frame time so a ``SwitchableLLMService`` flip mid-session
            is captured without rewiring.
        stt_provider
            Stable provider key for STT events (e.g. ``"groq"``). The
            STT path doesn't emit a Pipecat metrics class today, so the
            recorder needs the slug from configuration.
        tts_provider
            Stable provider key for TTS events (e.g. ``"edge_tts"``).
        """

        def __init__(
            self,
            *,
            store: Any,
            provider_getter: Any = None,
            stt_provider: str | None = None,
            tts_provider: str | None = None,
        ) -> None:
            super().__init__()
            self._store = store
            self._provider_getter = provider_getter or (lambda: "unknown")
            self._stt_provider = stt_provider
            self._tts_provider = tts_provider
            # Speech-bracket state — populated on UserStartedSpeaking,
            # consumed on UserStoppedSpeaking, then attached to the
            # next finalized TranscriptionFrame so audio_seconds is
            # accurate per utterance.
            self._speech_started_at: float | None = None
            self._last_speech_duration: float | None = None

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, MetricsFrame):
                for entry in frame.data or []:
                    if isinstance(entry, LLMUsageMetricsData):
                        self._handle_llm_metrics(entry)
                    elif isinstance(entry, TTSUsageMetricsData):
                        self._handle_tts_metrics(entry)
            elif isinstance(frame, UserStartedSpeakingFrame):
                self._speech_started_at = time.monotonic()
            elif isinstance(frame, UserStoppedSpeakingFrame):
                if self._speech_started_at is not None:
                    self._last_speech_duration = max(
                        0.0, time.monotonic() - self._speech_started_at
                    )
                    self._speech_started_at = None
            elif isinstance(frame, TranscriptionFrame):
                # Some pipecat STT services emit interim transcriptions
                # with finalized=False. Only count finals (or rows where
                # the attribute is missing on older versions) so we
                # don't inflate the call count.
                finalized = getattr(frame, "finalized", True)
                text = (getattr(frame, "text", "") or "").strip()
                if finalized and text:
                    self._handle_transcription(frame)

            await self.push_frame(frame, direction)

        # ----- LLM ---------------------------------------------------

        def _handle_llm_metrics(self, entry: Any) -> None:
            usage = entry.value
            model = entry.model
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
            cost = llm_cost(
                model=model, input_tokens=prompt, output_tokens=completion
            )
            provider = ""
            try:
                provider = self._provider_getter() or ""
            except Exception:
                logger.debug("provider_getter raised", exc_info=True)
            asyncio.create_task(self._record_llm(
                provider=provider, model=model,
                input_tokens=prompt, output_tokens=completion,
                cost_usd=cost,
            ))

        async def _record_llm(
            self,
            *,
            provider: str,
            model: str | None,
            input_tokens: int,
            output_tokens: int,
            cost_usd: float | None,
        ) -> None:
            try:
                await self._store.record_usage_event(
                    kind="llm",
                    provider=provider or None,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                )
            except Exception:
                logger.exception(
                    "[USAGE] failed to persist llm event "
                    "(provider=%s model=%s in=%d out=%d)",
                    provider, model, input_tokens, output_tokens,
                )

        # ----- TTS ---------------------------------------------------

        def _handle_tts_metrics(self, entry: Any) -> None:
            char_count = int(getattr(entry, "value", 0) or 0)
            if char_count <= 0:
                return
            # ``entry.processor`` is the Pipecat service class name; we
            # prefer the configured provider slug because pricing is
            # keyed by it.
            provider = self._tts_provider or getattr(entry, "processor", None)
            cost = tts_cost(provider=provider, char_count=char_count)
            # tts_cost returns None when char_count is 0; we already
            # filtered that. For unknown providers it returns None too —
            # we still record the call so volume statistics are
            # accurate even if dollars are unknown.
            asyncio.create_task(self._record_tts(
                provider=provider, char_count=char_count, cost_usd=cost,
            ))

        async def _record_tts(
            self, *, provider: str | None, char_count: int, cost_usd: float | None,
        ) -> None:
            try:
                await self._store.record_usage_event(
                    kind="tts",
                    provider=provider or None,
                    char_count=char_count,
                    cost_usd=cost_usd,
                )
            except Exception:
                logger.exception(
                    "[USAGE] failed to persist tts event "
                    "(provider=%s chars=%d)",
                    provider, char_count,
                )

        # ----- STT ---------------------------------------------------

        def _handle_transcription(self, frame: Any) -> None:
            duration = self._last_speech_duration
            self._last_speech_duration = None
            audio_seconds = float(duration) if duration is not None else 0.0
            cost = stt_cost(
                provider=self._stt_provider, audio_seconds=audio_seconds
            )
            asyncio.create_task(self._record_stt(
                provider=self._stt_provider,
                audio_seconds=audio_seconds, cost_usd=cost,
            ))

        async def _record_stt(
            self, *, provider: str | None, audio_seconds: float, cost_usd: float | None,
        ) -> None:
            try:
                await self._store.record_usage_event(
                    kind="stt",
                    provider=provider or None,
                    audio_seconds=audio_seconds,
                    cost_usd=cost_usd,
                )
            except Exception:
                logger.exception(
                    "[USAGE] failed to persist stt event "
                    "(provider=%s seconds=%.2f)",
                    provider, audio_seconds,
                )

    _processor_cls = UsageRecorder
    return _processor_cls


def create_usage_recorder(
    *,
    store: Any,
    provider_getter: Any = None,
    stt_provider: str | None = None,
    tts_provider: str | None = None,
):
    """Factory returning a UsageRecorder instance."""
    cls = _build_processor_class()
    return cls(
        store=store,
        provider_getter=provider_getter,
        stt_provider=stt_provider,
        tts_provider=tts_provider,
    )


__all__ = ["create_usage_recorder"]
