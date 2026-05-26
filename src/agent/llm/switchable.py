"""Runtime-switchable LLM service between OpenRouter, z.ai, and OpenCode Go."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    AuthenticationError,
)
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMMessagesFrame,
    StartFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame
from pipecat.metrics.metrics import MetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.llm_service import LLMService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.settings import LLMSettings

logger = logging.getLogger("heare.switchable_llm")


class SwitchableLLMService(LLMService):
    """LLM service that hot-swaps between OpenRouter (OpenAI shape), z.ai
    (Anthropic shape), and OpenCode Go (OpenAI shape) at runtime without
    rebuilding the pipeline.

    Architecture: composition over inheritance.
        - Holds up to three fully-formed Pipecat delegates: ``_or_service``
          (``OpenAILLMService``), ``_zai_service`` (``AnthropicLLMService``),
          and ``_oc_service`` (``OpenAILLMService``).
        - The wrapper itself is what the pipeline links into; the delegates
          are NOT linked into the pipeline graph (their ``_next``/``_prev`` are
          None). To make delegate-emitted frames reach downstream stages, this
          class patches each delegate's ``push_frame`` and ``broadcast_frame``
          to relay through the wrapper's own ``push_frame``/``broadcast_frame``
          (the "frame relay" mechanism).

    Provider routing:
        - ``~/.heare/provider`` (a single text file containing
          ``openrouter``, ``zai``, or ``opencode``) is the source of truth.
        - ``_sync_provider()`` re-reads the file lazily, gated on mtime, and
          ONLY at turn-start frames (``LLMContextFrame`` /
          ``OpenAILLMContextFrame`` / ``LLMMessagesFrame``) — never per-frame.
        - A sticky-turn gate locks the chosen delegate for the rest of the
          turn. The gate clears when the wrapper sees ``LLMFullResponseEndFrame``
          relayed from the delegate, so the next turn picks up any change.

    Tool registration:
        - ``register_function``/``unregister_function`` fan out to ALL
          delegates so the active provider always has the handler regardless
          of which one was active at boot.

    Failure mode:
        - z.ai auth/5xx errors fall back to OpenRouter for the remainder of
          the process, with a single ERROR log per 60-second window so the
          log file does not balloon.
    """

    def __init__(
        self,
        *,
        openrouter_api_key: str | None,
        openrouter_model: str,
        zai_api_key: str | None,
        zai_model: str,
        zai_base_url: str,
        opencode_api_key: str | None,
        opencode_base_url: str,
        opencode_model: str,
        provider_file: Path,
        **kwargs,
    ):
        """Initialize with all providers."""
        fallback_model = openrouter_model or zai_model or opencode_model
        wrapper_settings = LLMSettings(
            model=fallback_model,
            system_instruction=None,
            temperature=None,
            max_tokens=None,
            top_p=None,
            top_k=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            filter_incomplete_user_turns=False,
            user_turn_completion_config=None,
        )
        super().__init__(settings=wrapper_settings, **kwargs)

        self._or_service: OpenAILLMService | None = None
        if openrouter_api_key:
            self._or_service = OpenAILLMService(
                api_key=openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                settings=OpenAILLMService.Settings(model=openrouter_model),
                **kwargs,
            )
            self._install_frame_relay(self._or_service)

        self._zai_service: AnthropicLLMService | None = None
        if zai_api_key:
            self._zai_service = AnthropicLLMService(
                api_key=zai_api_key,
                settings=AnthropicLLMService.Settings(model=zai_model),
                client=AsyncAnthropic(api_key=zai_api_key, base_url=zai_base_url),
            )
            self._install_frame_relay(self._zai_service)

        self._oc_service: OpenAILLMService | None = None
        if opencode_api_key:
            self._oc_service = OpenAILLMService(
                api_key=opencode_api_key,
                base_url=opencode_base_url,
                settings=OpenAILLMService.Settings(model=opencode_model),
                **kwargs,
            )
            self._install_frame_relay(self._oc_service)

        # State variables for turn-gated switching and error recovery
        self._last_error_log_ts: float = 0.0
        self._zai_disabled: bool = False
        self._turn_in_flight: bool = False
        self._turn_delegate: Optional[LLMService] = None
        self._started_delegates: set[str] = set()
        self._delegate_setup: Optional[FrameProcessorSetup] = None
        self._saved_start_frame: Optional[Frame] = None

        available = []
        if openrouter_api_key:
            available.append("openrouter")
        if zai_api_key:
            available.append("zai")
        if opencode_api_key:
            available.append("opencode")
        if not available:
            raise ValueError(
                "At least one provider key (OPENROUTER_API_KEY, ZAI_API_KEY, "
                "or OPENCODE_API_KEY) must be set"
            )

        self._active_provider = available[0]
        if len(available) > 1:
            logger.info(
                "switchable_llm: multiple providers available (%s); "
                "defaulting to %s",
                ", ".join(available),
                self._active_provider,
            )

        self._or_model = openrouter_model
        self._zai_model = zai_model
        self._oc_model = opencode_model
        self._provider_file = provider_file
        self._provider_file_mtime: float = 0.0

    # --- Frame relay (Critical #1 fix) ---

    def _install_frame_relay(self, delegate):
        """Patch delegate's push_frame and broadcast_frame to relay through wrapper.

        Delegates are not linked into the pipeline (their _next/_prev are None),
        so frames they emit would otherwise be silently dropped. Relaying through
        the wrapper's own push_frame/broadcast_frame ensures downstream visibility.
        """
        wrapper = self

        async def relayed_push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            # Observe LLMFullResponseEndFrame to unlock sticky-turn gate
            if isinstance(frame, LLMFullResponseEndFrame):
                wrapper._turn_in_flight = False
                wrapper._turn_delegate = None
            await wrapper.push_frame(frame, direction)

        async def relayed_broadcast_frame(frame_cls, **kwargs):
            await wrapper.broadcast_frame(frame_cls, **kwargs)

        delegate.push_frame = relayed_push_frame
        delegate.broadcast_frame = relayed_broadcast_frame

    # --- Provider routing ---

    def _all_delegates(self) -> list[LLMService]:
        """Return all available delegates."""
        result: list[LLMService] = []
        if self._or_service is not None:
            result.append(self._or_service)
        if self._zai_service is not None and not self._zai_disabled:
            result.append(self._zai_service)
        if self._oc_service is not None:
            result.append(self._oc_service)
        return result

    def _delegate_for(self, provider: str) -> LLMService | None:
        if provider == "openrouter":
            return self._or_service
        if provider == "zai":
            return None if self._zai_disabled else self._zai_service
        if provider == "opencode":
            return self._oc_service
        return None

    def _provider_for_delegate(self, delegate: LLMService) -> str:
        """Return the provider name for a delegate instance."""
        if delegate is self._or_service:
            return "openrouter"
        if delegate is self._zai_service:
            return "zai"
        if delegate is self._oc_service:
            return "opencode"
        return "openrouter"

    def _first_available_provider(self) -> str:
        """Return the provider name of the first available delegate."""
        all_d = self._all_delegates()
        if all_d:
            return self._provider_for_delegate(all_d[0])
        return "openrouter"

    def _active_delegate(self) -> LLMService:
        """Return currently active delegate. Does NOT call _sync_provider."""
        d = self._delegate_for(self._active_provider)
        if d is not None:
            return d
        # Fallback: first available
        all_d = self._all_delegates()
        if all_d:
            return all_d[0]
        raise RuntimeError("no LLM delegate available")

    def _sync_provider(self) -> str:
        """Read provider file (mtime-gated). Returns provider name."""
        try:
            if not self._provider_file.exists():
                if not self._delegate_for(self._active_provider):
                    self._active_provider = self._first_available_provider()
                return self._active_provider

            current_mtime = os.path.getmtime(self._provider_file)
            if current_mtime == self._provider_file_mtime:
                return self._active_provider

            self._provider_file_mtime = current_mtime
            raw = self._provider_file.read_text().strip().lower()

            d = self._delegate_for(raw)
            if d is not None:
                self._active_provider = raw
                logger.info(
                    "switchable_llm: switched to %s provider (configured)", raw
                )
            else:
                fallback = self._first_available_provider()
                self._active_provider = fallback
                logger.warning(
                    "switchable_llm: provider %r unavailable, falling back to %s",
                    raw, fallback,
                )

        except Exception as e:
            logger.exception("switchable_llm: error reading provider file: %s", e)
            self._active_provider = self._first_available_provider()

        return self._active_provider

    @property
    def active_provider(self) -> str:
        """Return the currently active provider name."""
        self._sync_provider()
        return self._active_provider

    # --- Error handling ---

    def _is_provider_error(self, exc: Exception) -> bool:
        """Check if exception is a provider-level error vs a programming error."""
        return isinstance(
            exc,
            (
                AuthenticationError,
                APIStatusError,
                APIConnectionError,
                httpx.TimeoutException,
                httpx.ConnectError,
            ),
        )

    async def _handle_provider_failure(
        self, exc: Exception, frame: Frame, direction: FrameDirection
    ):
        """Handle provider-level errors with rate-limited logging and fallback."""
        now = time.time()
        # Rate-limit: log at most once per 60 seconds
        if now - self._last_error_log_ts > 60:
            logger.error(
                "switchable_llm: provider error on %s, falling back to openrouter: %s",
                self._active_provider,
                exc,
            )
            self._last_error_log_ts = now

        # Permanently disable z.ai for this process
        self._zai_disabled = True
        self._active_provider = "openrouter"
        self._turn_in_flight = False
        self._turn_delegate = None

        # Push ErrorFrame upstream for indication
        await self.push_frame(ErrorFrame(error=str(exc)))

        # Forward to OpenRouter for retry
        if self._or_service is not None:
            await self._or_service.process_frame(frame, direction)

    # --- Turn-gated process_frame (Critical #2 fix) ---

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # Let the wrapper handle StartFrame/CancelFrame/InterruptionFrame lifecycle
        await super().process_frame(frame, direction)

        # Turn-start: lock delegate, sync provider
        if isinstance(frame, (OpenAILLMContextFrame, LLMContextFrame, LLMMessagesFrame)):
            if not self._turn_in_flight:
                self._sync_provider()  # mtime-gated file read
                self._turn_delegate = self._active_delegate()
                self._turn_in_flight = True
                key = (
                    "zai" if self._turn_delegate is self._zai_service
                    else "oc" if self._turn_delegate is self._oc_service
                    else "or"
                )
                await self._ensure_delegate_started(self._turn_delegate, key)
                # Tag metrics with provider:model so dashboards can split costs/latency
                model = (
                    self._zai_model if self._active_provider == "zai"
                    else self._oc_model if self._active_provider == "opencode"
                    else self._or_model
                )
                self.set_core_metrics_data(
                    MetricsData(processor=self.name, model=f"{self._active_provider}:{model}")
                )

        delegate = (
            self._turn_delegate if self._turn_in_flight else self._active_delegate()
        )

        try:
            # Forward to delegate's process_frame so the delegate processes
            # the frame; emitted frames flow back via the patched push_frame relay.
            await delegate.process_frame(frame, direction)
        except Exception as e:
            if self._is_provider_error(e):
                await self._handle_provider_failure(e, frame, direction)
            else:
                raise

    # --- Lifecycle (Critical #4 fix: active-only start, fan-out stop) ---

    def _all_services(self) -> list[LLMService]:
        result: list[LLMService] = []
        if self._or_service is not None:
            result.append(self._or_service)
        if self._zai_service is not None:
            result.append(self._zai_service)
        if self._oc_service is not None:
            result.append(self._oc_service)
        return result

    async def setup(self, setup_obj: FrameProcessorSetup):
        """Receive pipeline setup; propagate to active delegate."""
        await super().setup(setup_obj)
        self._delegate_setup = setup_obj
        active = self._active_delegate()
        await active.setup(setup_obj)
        self._started_delegates = set()

    async def start(self, frame: StartFrame):
        """Start active delegate only. Inactive started lazily on first switch."""
        await super().start(frame)
        self._saved_start_frame = frame
        active = self._active_delegate()
        key = "zai" if active is self._zai_service else "oc" if active is self._oc_service else "or"
        await active.start(frame)
        self._started_delegates.add(key)

    async def stop(self, frame: EndFrame):
        for svc in self._all_services():
            await svc.stop(frame)

    async def cancel(self, frame: CancelFrame):
        for svc in self._all_services():
            await svc.cancel(frame)

    async def cleanup(self):
        await super().cleanup()
        for svc in self._all_services():
            await svc.cleanup()

    # --- Function registration fan-out ---

    def register_function(
        self, name, handler, *, cancel_on_interruption=True, **kw
    ):
        for svc in self._all_services():
            svc.register_function(
                name, handler, cancel_on_interruption=cancel_on_interruption, **kw
            )

    def unregister_function(self, name):
        for svc in self._all_services():
            svc.unregister_function(name)

    # --- Lazy delegate start on switch ---

    async def _ensure_delegate_started(self, delegate, key: str):
        if key not in self._started_delegates:
            await delegate.setup(self._delegate_setup)
            await delegate.start(self._saved_start_frame)
            self._started_delegates.add(key)
            logger.info("switchable_llm: lazily started %s delegate", key)
