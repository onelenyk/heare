"""Runtime-switchable LLM service between DeepSeek, z.ai, and OpenCode Go.

Fully data-driven: all provider metadata lives in :mod:`src.agent.llm.providers`.
Adding a new provider requires zero changes to this file — just add an entry
to the ``PROVIDERS`` registry.
"""
from __future__ import annotations

import logging
import time

import httpx
from anthropic import (
    APIConnectionError,
    APIStatusError,
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
from pipecat.metrics.metrics import MetricsData
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings

from src.agent.llm.providers import (
    PROVIDERS,
    make_anthropic_service,
    make_openai_service,
)

logger = logging.getLogger("heare.switchable_llm")


class SwitchableLLMService(LLMService):
    """LLM service that hot-swaps between providers at runtime without
    rebuilding the pipeline.

    Architecture: composition over inheritance.
        - Holds N fully-formed Pipecat delegates in ``_delegates`` (one per
          configured provider from the PROVIDERS registry).
        - The wrapper itself is what the pipeline links into; the delegates
          are NOT linked into the pipeline graph (their ``_next``/``_prev`` are
          None). To make delegate-emitted frames reach downstream stages, this
          class patches each delegate's ``push_frame`` and ``broadcast_frame``
          to relay through the wrapper's own ``push_frame``/``broadcast_frame``
          (the "frame relay" mechanism).

    Provider routing:
        - ``~/.heare/provider`` (a single text file containing a provider key
          like ``deepseek``, ``zai``, or ``opencode``) is the source of truth.
        - ``_sync_provider()`` re-reads the file lazily and ONLY at turn-start
          frames (``LLMContextFrame`` / ``OpenAILLMContextFrame`` /
          ``LLMMessagesFrame``) — never per-frame.
        - A sticky-turn gate locks the chosen delegate for the rest of the
          turn. The gate clears when the wrapper sees ``LLMFullResponseEndFrame``
          relayed from the delegate, so the next turn picks up any change.

    Tool registration:
        - ``register_function``/``unregister_function`` fan out to ALL
          delegates so the active provider always has the handler regardless
          of which one was active at boot.

    Failure mode:
        - Provider auth/5xx errors disable the failed provider and fall back
          to the first available alternative, with a single ERROR log per
          60-second window so the log file does not balloon.
    """

    def __init__(
        self,
        *,
        zai_api_key: str | None,
        zai_model: str,
        zai_base_url: str,
        opencode_api_key: str | None,
        opencode_base_url: str,
        opencode_model: str,
        deepseek_api_key: str | None,
        deepseek_base_url: str,
        deepseek_model: str,
        state,
        **kwargs,
    ):
        """Initialize delegates for every configured provider.

        Backward-compatible: *all* keyword params above are kept so
        existing callers do not break.  Internally only the api_key and
        model params are used; base URLs and model defaults come from the
        PROVIDERS registry (which is considered canonical).
        """
        # -- Build delegates from the registry --------------------------------
        self._delegates: dict[str, LLMService] = {}
        for key, cfg in PROVIDERS.items():
            api_key = locals().get(cfg.api_key_attr)
            if not api_key:
                continue
            model = locals().get(f"{key}_model", cfg.default_model)
            if cfg.api_style == "anthropic":
                svc = make_anthropic_service(cfg, api_key)
            else:
                svc = make_openai_service(cfg, api_key, model=model)
            self._install_frame_relay(svc)
            self._delegates[key] = svc

        if not self._delegates:
            raise ValueError("At least one provider API key must be set")

        # -- Wrapper settings (cosmetic — the wrapper is a router) ------------
        first_key = next(iter(self._delegates))
        fallback_model = PROVIDERS[first_key].default_model
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

        # -- State variables for turn-gated switching and error recovery ------
        self._last_error_log_ts: float = 0.0
        self._disabled_providers: set[str] = set()
        self._turn_in_flight: bool = False
        self._turn_delegate: LLMService | None = None
        self._started_delegates: set[str] = set()
        self._delegate_setup: FrameProcessorSetup | None = None
        self._saved_start_frame: Frame | None = None

        available = list(self._delegates.keys())
        self._active_provider = available[0]
        if len(available) > 1:
            logger.info(
                "switchable_llm: multiple providers available (%s); "
                "defaulting to %s",
                ", ".join(available),
                self._active_provider,
            )

        self._state = state

    # -- Backward-compatible property aliases (tests access these) ------------

    @property
    def _zai_service(self) -> LLMService | None:
        """Backward-compat: returns the z.ai delegate or None."""
        return self._delegates.get("zai")

    @property
    def _oc_service(self) -> LLMService | None:
        """Backward-compat: returns the OpenCode Go delegate or None."""
        return self._delegates.get("opencode")

    @property
    def _deepseek_service(self) -> LLMService | None:
        """Backward-compat: returns the DeepSeek delegate or None."""
        return self._delegates.get("deepseek")

    @property
    def _zai_disabled(self) -> bool:
        """Backward-compat: True when z.ai has been disabled by error handling."""
        return "zai" in self._disabled_providers

    @_zai_disabled.setter
    def _zai_disabled(self, value: bool) -> None:
        if value:
            self._disabled_providers.add("zai")
        else:
            self._disabled_providers.discard("zai")

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
        """Return all available (non-disabled) delegates."""
        return [
            svc
            for key, svc in self._delegates.items()
            if key not in self._disabled_providers
        ]

    def _delegate_for(self, provider: str) -> LLMService | None:
        """Look up a delegate by provider key, respecting disabled state."""
        if provider in self._disabled_providers:
            return None
        return self._delegates.get(provider)

    def _provider_for_delegate(self, delegate: LLMService) -> str:
        """Return the provider key for a delegate instance (identity check)."""
        for key, svc in self._delegates.items():
            if svc is delegate:
                return key
        return "deepseek"

    def _first_available_provider(self) -> str:
        """Return the provider key of the first available delegate."""
        all_d = self._all_delegates()
        if all_d:
            return self._provider_for_delegate(all_d[0])
        return "deepseek"

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
        """Read provider from state. Returns provider key."""
        raw = self._state.get("provider").strip().lower()
        if raw and raw != self._active_provider:
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
                    raw,
                    fallback,
                )
        return self._active_provider

    @property
    def active_provider(self) -> str:
        """Return the currently active provider key."""
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
                "switchable_llm: provider error on %s, disabling it: %s",
                self._active_provider,
                exc,
            )
            self._last_error_log_ts = now

        # Disable current provider and fall back to first available
        self._disabled_providers.add(self._active_provider)
        fallback = self._first_available_provider()
        self._active_provider = fallback
        self._turn_in_flight = False
        self._turn_delegate = None

        # Push ErrorFrame upstream for indication
        await self.push_frame(ErrorFrame(error=str(exc)))

        # Forward to fallback delegate for retry
        fallback_delegate = self._delegates.get(fallback)
        if fallback_delegate is not None:
            await fallback_delegate.process_frame(frame, direction)

    # --- Turn-gated process_frame (Critical #2 fix) ---

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # Let the wrapper handle StartFrame/CancelFrame/InterruptionFrame lifecycle
        await super().process_frame(frame, direction)

        # Turn-start: lock delegate, sync provider
        if isinstance(frame, (OpenAILLMContextFrame, LLMContextFrame, LLMMessagesFrame)):
            if not self._turn_in_flight:
                self._sync_provider()  # reads provider file
                self._turn_delegate = self._active_delegate()
                self._turn_in_flight = True
                key = self._provider_for_delegate(self._turn_delegate)
                await self._ensure_delegate_started(self._turn_delegate, key)
                # Tag metrics with provider:model so dashboards can split costs/latency
                cfg = PROVIDERS[self._active_provider]
                self.set_core_metrics_data(
                    MetricsData(
                        processor=self.name,
                        model=f"{cfg.key}:{cfg.default_model}",
                    )
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
        """Return all delegates (including disabled) for lifecycle fan-out."""
        return list(self._delegates.values())

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
        key = self._provider_for_delegate(active)
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
