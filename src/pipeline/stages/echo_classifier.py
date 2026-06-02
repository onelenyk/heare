"""LLM-based echo classifier — semantically classify transcriptions as echo or real speech.

While the bot is speaking, each ``TranscriptionFrame`` is sent to the DeepSeek
LLM with a simple prompt: "Is this ECHO (my voice bleeding back through mic)
or a real INTERRUPTION?" The LLM replies ECHO or INTERRUPT. ECHO frames are
dropped; real interruptions pass through.

This is a text-level supplement to the audio-level ``MicEchoGate``
cross-correlation detector. Together they provide defence-in-depth against
the speaker-to-mic feedback loop that otherwise makes open-mic barge-in
without headphones impractical.

Pipecat imports are deferred so admin CLI paths work without portaudio.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from src.config import Settings


logger = logging.getLogger("heare.echo_classifier")


# ---------------------------------------------------------------------------
# Deferred pipecat imports (keeps admin CLI working without portaudio)
# ---------------------------------------------------------------------------

def _load_pipecat_base():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InterruptionFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    return (
        FrameProcessor,
        FrameDirection,
        Frame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InterruptionFrame,
        TranscriptionFrame,
    )


_processor_cls: type | None = None


def _build_processor_class():
    global _processor_cls
    if _processor_cls is not None:
        return _processor_cls

    (
        FrameProcessor,
        FrameDirection,
        Frame,
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InterruptionFrame,
        TranscriptionFrame,
    ) = _load_pipecat_base()

    class EchoClassifier(FrameProcessor):  # type: ignore[misc,valid-type]
        """LLM-based echo classifier that drops echoed transcription frames.

        Tracks bot speaking state via Pipecat system frames. When the bot is
        speaking and a TranscriptionFrame arrives, queries the LLM to decide
        whether it's echo (the bot's own voice bleeding back through the mic)
        or a real human interruption.

        Constructor params are all optional with defaults of None to
        support dependency injection at pipeline build time.
        """

        def __init__(
            self,
            *,
            state: Any = None,
            bot_speech_state: Any = None,
            settings: "Settings | None" = None,
        ) -> None:
            super().__init__()
            self._state = state
            self._bot_speech_state = bot_speech_state
            self._settings = settings
            self._bot_speaking = False
            self._classifying = False
            self._echo_dropped = 0
            self._echo_passed = 0

        async def process_frame(self, frame: Any, direction: Any) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
                await self.push_frame(frame, direction)
                return

            if isinstance(frame, InterruptionFrame):
                self._bot_speaking = False
                await self.push_frame(frame, direction)
                return

            if not isinstance(frame, TranscriptionFrame):
                await self.push_frame(frame, direction)
                return

            if not self._bot_speaking:
                await self.push_frame(frame, direction)
                return

            if self._settings is None:
                await self.push_frame(frame, direction)
                return

            # echo_classifier_enabled is added by a parallel task; use
            # getattr as a safe default when the field hasn't landed yet.
            enabled = getattr(self._settings, "echo_classifier_enabled", True)
            if not enabled:
                await self.push_frame(frame, direction)
                return

            transcript = (frame.text or "").strip()
            bot_text = (
                self._bot_speech_state.text
                if self._bot_speech_state is not None
                else ""
            )
            if not transcript or not bot_text:
                await self.push_frame(frame, direction)
                return

            if self._classifying:
                await self.push_frame(frame, direction)
                return
            self._classifying = True

            prompt = (
                f"I'm speaking. I just said: '{bot_text}'."
                f" The user said: '{transcript}'."
                f" Is this ECHO (my voice bleeding back through mic) or a"
                f" real INTERRUPTION? Reply ECHO or INTERRUPT only."
            )

            timeout = getattr(self._settings, "deepseek_timeout_seconds", 3.0)
            try:
                result = await asyncio.wait_for(
                    self._classify_echo(prompt), timeout=timeout
                )
                if result.strip().upper().startswith("ECHO"):
                    self._echo_dropped += 1
                    logger.debug(
                        "[ECHO CLASSIFIER] dropped echo (%d): %s",
                        self._echo_dropped,
                        transcript[:80],
                    )
                    return
                self._echo_passed += 1
                await self.push_frame(frame, direction)
            except asyncio.TimeoutError:
                logger.warning(
                    "[ECHO CLASSIFIER] LLM call timed out after %.1fs",
                    timeout,
                )
                await self.push_frame(frame, direction)
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                logger.warning(
                    "[ECHO CLASSIFIER] LLM call failed: %s", e
                )
                await self.push_frame(frame, direction)
            finally:
                self._classifying = False

        async def _classify_echo(self, prompt: str) -> str:
            """Call the DeepSeek LLM to classify a transcription as ECHO or INTERRUPT.

            Uses the direct httpx.AsyncClient pattern from ``providers.py``
            to avoid pulling in Pipecat's LLM service machinery.
            """
            api_key = self._settings.deepseek_api_key  # type: ignore[union-attr]
            base_url = getattr(
                self._settings,
                "deepseek_base_url",
                "https://api.deepseek.com/v1",
            )
            model = getattr(
                self._settings, "deepseek_model", "deepseek-chat"
            )

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an echo detector."
                            " Reply ECHO or INTERRUPT only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 5,
                "temperature": 0,
            }

            timeout = getattr(self._settings, "deepseek_timeout_seconds", 3.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            return data["choices"][0]["message"]["content"].strip().upper()

    _processor_cls = EchoClassifier
    return _processor_cls


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_echo_classifier(
    *,
    state=None,
    bot_speech_state=None,
    settings=None,
):
    """Factory returning an EchoClassifier instance.

    Insert the classifier downstream of STT (after ``TranscriptionFrame``
    generation) and upstream of the user aggregator so that echoed speech
    is dropped before it can trigger inappropriate LLM responses.
    """
    cls = _build_processor_class()
    return cls(state=state, bot_speech_state=bot_speech_state, settings=settings)


__all__ = ["create_echo_classifier"]
