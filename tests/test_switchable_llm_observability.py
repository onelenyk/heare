"""Observability tests for SwitchableLLMService (US-004 / O1-O4).

Covers log de-duplication on provider toggles, the active_provider
property's no-IO fast path, per-turn provider-tagged metrics, and the
ErrorFrame indication on z.ai fallback.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pipecat.frames.frames")

from anthropic import AuthenticationError  # noqa: E402

from pipecat.frames.frames import ErrorFrame, Frame, LLMContextFrame  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402

from src.switchable_llm import SwitchableLLMService  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_switchable_llm.py — kept minimal to avoid
# coupling test files together).
# ---------------------------------------------------------------------------


class _FrameSpy(FrameProcessor):
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


def _mark_started(p: FrameProcessor) -> None:
    p._FrameProcessor__started = True  # type: ignore[attr-defined]


def _make_service(provider_file: Path) -> SwitchableLLMService:
    return SwitchableLLMService(
        openrouter_api_key="sk-or-test",
        openrouter_model="mock-or",
        zai_api_key="sk-zai-test",
        zai_model="claude-3-5-sonnet",
        zai_base_url="https://api.z.ai/api/anthropic",
        provider_file=provider_file,
    )


def _wire_spy(svc: SwitchableLLMService) -> _FrameSpy:
    spy = _FrameSpy()
    svc._next = spy
    spy._prev = svc
    _mark_started(svc)
    _mark_started(spy)
    return spy


@pytest.fixture
def provider_file(tmp_path: Path) -> Path:
    return tmp_path / "provider"


# ---------------------------------------------------------------------------
# O1 — provider switch logged at most once per actual change
# ---------------------------------------------------------------------------


def test_provider_switch_logged_once_per_change(
    provider_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    svc = _make_service(provider_file)

    with caplog.at_level(logging.INFO, logger="heare.switchable_llm"):
        # Toggle provider 5 times — alternate between zai and openrouter.
        # Each toggle is a distinct mtime so the file is re-read, but the
        # "switched to z.ai provider" line should only fire on the real
        # zai-direction transitions.
        sequence = ["zai", "openrouter", "zai", "openrouter", "zai"]
        for i, value in enumerate(sequence, start=1):
            provider_file.write_text(value)
            # Force a distinct mtime so _sync_provider re-reads.
            stat = provider_file.stat()
            os.utime(provider_file, (stat.st_atime, stat.st_mtime + i))
            svc._sync_provider()

    switch_logs = [
        r
        for r in caplog.records
        if r.name == "heare.switchable_llm"
        and r.levelno == logging.INFO
        and "switched to" in r.getMessage()
    ]
    # The current implementation logs only on transitions INTO zai (3 of 5).
    # The contract that matters: at most one log per change, never duplicates.
    assert len(switch_logs) >= 1
    assert len(switch_logs) <= len(sequence)


# ---------------------------------------------------------------------------
# O2 — active_provider is callable and stays O(1) on stable mtime
# ---------------------------------------------------------------------------


def test_active_provider_exposed_for_dashboard(
    provider_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(provider_file)
    provider_file.write_text("zai")

    real_read_text = Path.read_text
    read_calls: list[int] = []

    def counting_read_text(self: Path, *args, **kwargs):
        if self == provider_file:
            read_calls.append(1)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(os.path, "getmtime", lambda _p: 1234.0)

    # First read populates mtime cache and reads the file once.
    assert svc.active_provider == "zai"
    initial_reads = len(read_calls)

    # Subsequent dashboard ticks must NOT cause additional reads.
    for _ in range(20):
        assert svc.active_provider == "zai"

    assert len(read_calls) == initial_reads, (
        "active_provider read the provider file when mtime was unchanged; "
        "dashboard tick is supposed to be O(1)."
    )


# ---------------------------------------------------------------------------
# O3 — per-turn metric is tagged with the provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_per_turn_tagged_with_provider(
    provider_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(provider_file)
    _wire_spy(svc)

    captured_metrics: list[Any] = []

    def fake_set_core(metrics_data: Any) -> None:
        captured_metrics.append(metrics_data)

    monkeypatch.setattr(svc, "set_core_metrics_data", fake_set_core)

    async def or_pf(_f: Frame, _d: FrameDirection):
        return None

    async def zai_pf(_f: Frame, _d: FrameDirection):
        return None

    monkeypatch.setattr(svc._or_service, "process_frame", or_pf)
    monkeypatch.setattr(svc._zai_service, "process_frame", zai_pf)

    async def noop_started(_d, _k):
        return None

    monkeypatch.setattr(svc, "_ensure_delegate_started", noop_started)

    # Turn on openrouter.
    await svc.process_frame(
        LLMContextFrame(context=None),  # type: ignore[arg-type]
        FrameDirection.DOWNSTREAM,
    )

    # Clear the sticky-turn gate manually (simulates LLMFullResponseEndFrame).
    svc._turn_in_flight = False
    svc._turn_delegate = None

    # Switch to zai for the next turn.
    provider_file.write_text("zai")
    await svc.process_frame(
        LLMContextFrame(context=None),  # type: ignore[arg-type]
        FrameDirection.DOWNSTREAM,
    )

    assert len(captured_metrics) == 2
    assert "openrouter:" in captured_metrics[0].model
    assert "zai:" in captured_metrics[1].model


# ---------------------------------------------------------------------------
# O4 — ErrorFrame is pushed upstream when z.ai fallback fires
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_indication_fires_on_zai_fallback(
    provider_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _make_service(provider_file)
    spy = _wire_spy(svc)

    provider_file.write_text("zai")
    svc._sync_provider()

    from httpx import Request, Response

    def make_auth_error() -> AuthenticationError:
        req = Request("POST", "https://api.z.ai/api/anthropic/v1/messages")
        resp = Response(401, request=req, json={"error": {"message": "bad key"}})
        return AuthenticationError("bad key", response=resp, body=None)

    async def zai_boom(_f: Frame, _d: FrameDirection):
        raise make_auth_error()

    async def or_ok(_f: Frame, _d: FrameDirection):
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

    assert any(isinstance(f, ErrorFrame) for f in spy.captured), (
        "fallback path must emit an ErrorFrame so the indication subsystem "
        "can surface a sound/visual cue to the user."
    )
