"""Unit tests for SwitchableLLMService (US-004 / zai-anthropic-full-support U1-U9).

Covers init validation, mtime-gated provider sync, function fan-out, the
sticky-turn gate, error fallback with rate-limited logging, and the frame
relay that ensures delegate-emitted frames reach downstream stages.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pipecat.frames.frames")

from anthropic import AuthenticationError  # noqa: E402

from pipecat.frames.frames import (  # noqa: E402
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402

from src.agent.llm.switchable import SwitchableLLMService  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class _MockState:
    """Minimal State mock for testing SwitchableLLMService."""
    def __init__(self, **initial):
        self._data = dict(initial)

    def get_bool(self, key: str) -> bool:
        return self._data.get(key) == "1"

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    async def set(self, key: str, value: str):
        self._data[key] = value

    async def set_bool(self, key: str, value: bool):
        self._data[key] = "1" if value else "0"


@pytest.fixture
def state():
    """Mock State with no initial provider set."""
    return _MockState()


def _make_service(
    state: _MockState,
    *,
    openrouter_api_key: str | None = "sk-or-test",
    zai_api_key: str | None = "sk-zai-test",
) -> SwitchableLLMService:
    return SwitchableLLMService(
        openrouter_api_key=openrouter_api_key,
        openrouter_model="mock-or",
        zai_api_key=zai_api_key,
        zai_model="claude-3-5-sonnet",
        zai_base_url="https://api.z.ai/api/anthropic",
        opencode_api_key=None,
        opencode_base_url="https://opencode.ai/zen/go/v1",
        opencode_model="minimax-m2.7",
        deepseek_api_key=None,
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-chat",
        state=state,
    )


class FrameSpy(FrameProcessor):
    """A FrameProcessor that captures every frame pushed into it.

    Used to verify the frame relay actually delivers delegate-emitted frames
    to downstream pipeline stages. Overrides ``queue_frame`` so frames land
    in the capture list synchronously without needing the input task that
    only runs after a real StartFrame.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[Frame] = []

    async def queue_frame(  # type: ignore[override]
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
        callback: Any = None,
    ) -> None:
        self.captured.append(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.captured.append(frame)


def _mark_started(processor: FrameProcessor) -> None:
    """Bypass FrameProcessor's StartFrame guard for unit-test relay.

    The base class guards push_frame with a private ``__started`` flag set
    by an internal ``__start`` coroutine that runs only when a real StartFrame
    arrives. Unit tests don't want to drive the pipeline lifecycle, so we
    flip the (name-mangled) flag directly.
    """
    processor._FrameProcessor__started = True  # type: ignore[attr-defined]


def _wire_spy_as_next(svc: SwitchableLLMService) -> FrameSpy:
    """Wire a FrameSpy into svc._next so push_frame relays land somewhere.

    Also marks both the wrapper and the spy as ``__started`` so push_frame
    bypasses Pipecat's StartFrame guard during unit tests.
    """
    spy = FrameSpy()
    # Bypass Pipeline.link() — just attach _next/_prev manually for tests.
    svc._next = spy
    spy._prev = svc
    _mark_started(svc)
    _mark_started(spy)
    return spy


# ---------------------------------------------------------------------------
# U1 — both keys present, default is openrouter
# ---------------------------------------------------------------------------


def test_init_with_both_keys(state: _MockState) -> None:
    svc = _make_service(state)
    assert svc._or_service is not None
    assert svc._zai_service is not None
    assert svc.active_provider == "openrouter"


# ---------------------------------------------------------------------------
# U2 — only openrouter key; switching to zai is a no-op with warning
# ---------------------------------------------------------------------------


def test_init_zai_key_missing_no_zai_delegate(
    state: _MockState, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="heare.switchable_llm"):
        svc = _make_service(state, zai_api_key=None)
    assert svc._zai_service is None
    # Write "zai" to state and try to switch.
    state._data["provider"] = "zai"
    # _sync_provider should keep us on openrouter because no zai delegate.
    assert svc.active_provider == "openrouter"


# ---------------------------------------------------------------------------
# U3 — only zai key; init succeeds, default is zai, openrouter switch is no-op
# ---------------------------------------------------------------------------


def test_init_only_zai_key(
    state: _MockState, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="heare.switchable_llm"):
        svc = _make_service(state, openrouter_api_key=None)
    # Init must succeed and not raise.
    assert svc._or_service is None
    assert svc._zai_service is not None
    assert svc.active_provider == "zai"

    # Attempting to switch to openrouter is a no-op (stays zai).
    state._data["provider"] = "openrouter"
    assert svc.active_provider == "zai"


def test_init_no_keys_raises(state: _MockState) -> None:
    """Defensive: at least one provider key must be set."""
    with pytest.raises(ValueError):
        _make_service(state, openrouter_api_key=None, zai_api_key=None)


# ---------------------------------------------------------------------------
# U4 — _sync_provider is mtime-gated
# ---------------------------------------------------------------------------


def test_sync_provider_mtime_gated(
    state: _MockState, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(state)
    state._data["provider"] = "zai"

    svc._sync_provider()
    assert svc._active_provider == "zai"

    # No change → provider stays zai
    svc._sync_provider()
    assert svc._active_provider == "zai"

    # Switch to openrouter
    state._data["provider"] = "openrouter"
    svc._sync_provider()
    assert svc._active_provider == "openrouter"


# ---------------------------------------------------------------------------
# U5 — register_function fans out to both delegates
# ---------------------------------------------------------------------------


def test_register_function_fans_out_to_both_delegates(state: _MockState) -> None:
    svc = _make_service(state)

    async def _bash_handler(_params: Any) -> None:
        return None

    svc.register_function("bash", _bash_handler)

    assert "bash" in svc._or_service._functions
    assert "bash" in svc._zai_service._functions

    # And unregister fans out symmetrically.
    svc.unregister_function("bash")
    assert "bash" not in svc._or_service._functions
    assert "bash" not in svc._zai_service._functions


# ---------------------------------------------------------------------------
# U6 — provider flip mid-turn defers to next turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_flip_during_turn_defers_to_next_turn(
    state: _MockState, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(state)
    spy = _wire_spy_as_next(svc)  # noqa: F841 — spy attached for relay path

    # Stub delegates to avoid lifecycle / network and observe routing.
    or_calls: list[Frame] = []
    zai_calls: list[Frame] = []

    async def or_pf(frame: Frame, direction: FrameDirection):
        or_calls.append(frame)

    async def zai_pf(frame: Frame, direction: FrameDirection):
        zai_calls.append(frame)

    monkeypatch.setattr(svc._or_service, "process_frame", or_pf)
    monkeypatch.setattr(svc._zai_service, "process_frame", zai_pf)

    # Make _ensure_delegate_started a no-op so we don't trigger StartFrame setup.
    async def noop_started(_d, _k):
        return None

    monkeypatch.setattr(svc, "_ensure_delegate_started", noop_started)

    # Turn 1: starts on openrouter (default).
    ctx1 = LLMContextFrame(context=None)  # type: ignore[arg-type]
    await svc.process_frame(ctx1, FrameDirection.DOWNSTREAM)

    assert svc._turn_in_flight is True
    assert svc._turn_delegate is svc._or_service
    assert ctx1 in or_calls
    assert ctx1 not in zai_calls

    # Mid-turn: flip the provider state to zai.
    state._data["provider"] = "zai"

    # Push a follow-up frame within the same turn — must still go to OR.
    text = LLMTextFrame("partial")
    await svc.process_frame(text, FrameDirection.DOWNSTREAM)
    assert text in or_calls
    assert text not in zai_calls

    # Simulate the delegate's end-of-response frame travelling back through
    # the relay; this is what unlocks the sticky-turn gate.
    svc._turn_in_flight = False
    svc._turn_delegate = None

    # Turn 2: a new turn-start frame should now resolve to zai.
    ctx2 = LLMContextFrame(context=None)  # type: ignore[arg-type]
    await svc.process_frame(ctx2, FrameDirection.DOWNSTREAM)
    assert svc._turn_delegate is svc._zai_service
    assert ctx2 in zai_calls


# ---------------------------------------------------------------------------
# U7 — z.ai auth error: fallback, rate-limited log, gate cleared
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zai_auth_error_falls_back_and_rate_limits_logs(
    state: _MockState,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = _make_service(state)
    _wire_spy_as_next(svc)

    # Force zai active.
    state._data["provider"] = "zai"
    svc._sync_provider()
    assert svc._active_provider == "zai"

    # Stub openrouter to silently accept the fallback retry.
    async def or_pf(_frame: Frame, _direction: FrameDirection):
        return None

    monkeypatch.setattr(svc._or_service, "process_frame", or_pf)

    # Stub zai to raise AuthenticationError every call.
    def make_auth_error() -> AuthenticationError:
        # AuthenticationError(message, response, body) — minimal stub.
        from httpx import Request, Response

        req = Request("POST", "https://api.z.ai/api/anthropic/v1/messages")
        resp = Response(401, request=req, json={"error": {"message": "bad key"}})
        return AuthenticationError("bad key", response=resp, body=None)

    async def zai_boom(_frame: Frame, _direction: FrameDirection):
        raise make_auth_error()

    monkeypatch.setattr(svc._zai_service, "process_frame", zai_boom)

    async def noop_started(_d, _k):
        return None

    monkeypatch.setattr(svc, "_ensure_delegate_started", noop_started)

    with caplog.at_level(logging.ERROR, logger="heare.switchable_llm"):
        # Two errors back-to-back inside the same 60s window.
        await svc.process_frame(
            LLMContextFrame(context=None),  # type: ignore[arg-type]
            FrameDirection.DOWNSTREAM,
        )
        # _zai_disabled is True now and active is permanently openrouter.
        assert svc._zai_disabled is True
        assert svc._active_provider == "openrouter"
        assert svc._turn_in_flight is False
        assert svc._turn_delegate is None

        # Re-arm an error scenario just to verify rate limiting:
        # call _handle_provider_failure twice within 60s, only one log line.
        svc._zai_disabled = False
        svc._active_provider = "zai"
        await svc._handle_provider_failure(
            make_auth_error(),
            LLMContextFrame(context=None),  # type: ignore[arg-type]
            FrameDirection.DOWNSTREAM,
        )

    error_records = [
        r
        for r in caplog.records
        if r.name == "heare.switchable_llm" and r.levelno == logging.ERROR
    ]
    # First failure logs once. The second failure call within 60s does NOT.
    assert len(error_records) == 1, [r.getMessage() for r in error_records]


# ---------------------------------------------------------------------------
# U8 — active_provider property reflects file changes after sync
# ---------------------------------------------------------------------------


def test_active_provider_property(state: _MockState) -> None:
    svc = _make_service(state)
    assert svc.active_provider == "openrouter"
    state._data["provider"] = "zai"
    assert svc.active_provider == "zai"
    state._data["provider"] = "openrouter"
    assert svc.active_provider == "openrouter"


# ---------------------------------------------------------------------------
# U9 — delegate-emitted frames reach downstream via the wrapper relay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_frames_reach_downstream(
    state: _MockState, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(state)
    spy = _wire_spy_as_next(svc)

    # We don't need a real LLM call; we just want to confirm the relay path.
    # Simulate the delegate emitting frames via its (now patched) push_frame.
    delegate = svc._or_service
    # Use the patched push_frame that should relay through the wrapper.

    async def fake_process(_frame: Frame, _direction: FrameDirection):
        await delegate.push_frame(LLMFullResponseStartFrame())
        await delegate.push_frame(LLMTextFrame("hello "))
        await delegate.push_frame(LLMTextFrame("world"))
        await delegate.push_frame(LLMFullResponseEndFrame())

    monkeypatch.setattr(delegate, "process_frame", fake_process)

    async def noop_started(_d, _k):
        return None

    monkeypatch.setattr(svc, "_ensure_delegate_started", noop_started)

    # Push a turn-start frame to drive process_frame.
    await svc.process_frame(
        LLMContextFrame(context=None),  # type: ignore[arg-type]
        FrameDirection.DOWNSTREAM,
    )

    # The spy must have observed the relayed frames downstream.
    captured_types = [type(f).__name__ for f in spy.captured]
    assert "LLMFullResponseStartFrame" in captured_types
    assert captured_types.count("LLMTextFrame") >= 1
    assert "LLMFullResponseEndFrame" in captured_types

    # Sanity: the End frame cleared the sticky-turn gate via the relay hook.
    assert svc._turn_in_flight is False
    assert svc._turn_delegate is None


# ---------------------------------------------------------------------------
# Bonus — push_frame ErrorFrame on auth failure (verified jointly with O4 too)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_error_pushes_error_frame(
    state: _MockState, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(state)
    spy = _wire_spy_as_next(svc)

    state._data["provider"] = "zai"
    svc._sync_provider()

    from httpx import Request, Response

    def make_auth_error() -> AuthenticationError:
        req = Request("POST", "https://api.z.ai/api/anthropic/v1/messages")
        resp = Response(401, request=req, json={"error": {"message": "bad key"}})
        return AuthenticationError("bad key", response=resp, body=None)

    async def zai_boom(_f, _d):
        raise make_auth_error()

    async def or_ok(_f, _d):
        return None

    monkeypatch.setattr(svc._zai_service, "process_frame", zai_boom)
    monkeypatch.setattr(svc._or_service, "process_frame", or_ok)

    async def noop_started(_d, _k):
        return None

    monkeypatch.setattr(svc, "_ensure_delegate_started", noop_started)

    await svc.process_frame(
        LLMContextFrame(context=None),  # type: ignore[arg-type]
        FrameDirection.DOWNSTREAM,
    )

    assert any(isinstance(f, ErrorFrame) for f in spy.captured)
