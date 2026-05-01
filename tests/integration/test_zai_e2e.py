"""End-to-end z.ai delegation tests (US-004 / E1-E3).

Real network calls to z.ai's Anthropic-compatible endpoint. The whole
module is gated on ``ZAI_API_KEY`` so CI / local runs without the env
var skip cleanly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Module-level skip if the env var is unset — keeps CI deterministic.
if not os.environ.get("ZAI_API_KEY"):
    pytest.skip(
        "ZAI_API_KEY not set; skipping z.ai end-to-end tests.",
        allow_module_level=True,
    )

pytest.importorskip("pipecat.frames.frames")

from pipecat.frames.frames import (  # noqa: E402
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMContextFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402

from src.switchable_llm import SwitchableLLMService  # noqa: E402

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Spy(FrameProcessor):
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


def _build_service(tmp_path: Path) -> SwitchableLLMService:
    return SwitchableLLMService(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "sk-or-stub"),
        openrouter_model=os.environ.get(
            "HEARE_OPENROUTER_MODEL", "google/gemini-2.0-flash-001"
        ),
        zai_api_key=os.environ["ZAI_API_KEY"],
        zai_model=os.environ.get("HEARE_ZAI_MODEL", "claude-3-5-sonnet"),
        zai_base_url=os.environ.get(
            "HEARE_ZAI_BASE_URL", "https://api.z.ai/api/anthropic"
        ),
        provider_file=tmp_path / "provider",
    )


def _wire(svc: SwitchableLLMService) -> _Spy:
    spy = _Spy()
    svc._next = spy
    spy._prev = svc
    _mark_started(svc)
    _mark_started(spy)
    return spy


# ---------------------------------------------------------------------------
# E1 — simple completion via z.ai
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zai_simple_completion(tmp_path: Path) -> None:
    svc = _build_service(tmp_path)
    spy = _wire(svc)

    # Force zai active.
    svc._provider_file.write_text("zai")
    svc._sync_provider()
    assert svc._active_provider == "zai"

    # Build a minimal LLMContext with a single user message.
    from pipecat.processors.aggregators.llm_context import LLMContext

    context = LLMContext()
    context.add_message({"role": "user", "content": "Say the word 'pong' and nothing else."})

    await svc.process_frame(
        LLMContextFrame(context=context),
        FrameDirection.DOWNSTREAM,
    )

    text_frames = [f for f in spy.captured if isinstance(f, LLMTextFrame)]
    assert text_frames, "expected at least one LLMTextFrame from z.ai"
    assert any(f.text and f.text.strip() for f in text_frames)


# ---------------------------------------------------------------------------
# E2 — tool call roundtrip with downstream visibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zai_tool_call_roundtrip(tmp_path: Path) -> None:
    svc = _build_service(tmp_path)
    spy = _wire(svc)

    # Register a simple bash handler.
    async def bash_handler(params: Any) -> None:
        cmd = (params.arguments or {}).get("command", "")
        await params.result_callback(
            {"success": True, "output": f"ran: {cmd}", "summary": "ran command"}
        )

    svc.register_function("bash", bash_handler)

    svc._provider_file.write_text("zai")
    svc._sync_provider()

    from pipecat.processors.aggregators.llm_context import LLMContext

    context = LLMContext()
    context.add_message(
        {
            "role": "system",
            "content": "When the user asks to list files, call the bash tool with command='ls'.",
        }
    )
    context.add_message({"role": "user", "content": "List files in the current directory."})

    await svc.process_frame(
        LLMContextFrame(context=context),
        FrameDirection.DOWNSTREAM,
    )

    types_seen = [type(f).__name__ for f in spy.captured]
    assert any(isinstance(f, FunctionCallInProgressFrame) for f in spy.captured), (
        f"expected FunctionCallInProgressFrame; saw {types_seen}"
    )
    assert any(isinstance(f, FunctionCallResultFrame) for f in spy.captured), (
        f"expected FunctionCallResultFrame; saw {types_seen}"
    )
    assert any(isinstance(f, LLMTextFrame) for f in spy.captured), (
        f"expected final LLMTextFrame after tool result; saw {types_seen}"
    )


# ---------------------------------------------------------------------------
# E3 — provider swap mid-conversation lands on z.ai for the third turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_swap_midconversation_e2e(tmp_path: Path) -> None:
    svc = _build_service(tmp_path)
    spy = _wire(svc)

    # Capture which delegate served which turn.
    or_calls = 0
    zai_calls = 0
    real_or = svc._or_service.process_frame
    real_zai = svc._zai_service.process_frame

    async def or_wrapped(frame: Frame, direction: FrameDirection):
        nonlocal or_calls
        or_calls += 1
        await real_or(frame, direction)

    async def zai_wrapped(frame: Frame, direction: FrameDirection):
        nonlocal zai_calls
        zai_calls += 1
        await real_zai(frame, direction)

    svc._or_service.process_frame = or_wrapped  # type: ignore[method-assign]
    svc._zai_service.process_frame = zai_wrapped  # type: ignore[method-assign]

    from pipecat.processors.aggregators.llm_context import LLMContext

    # Turns 1 and 2 on openrouter (default).
    svc._provider_file.write_text("openrouter")
    svc._sync_provider()
    for _ in range(2):
        ctx = LLMContext()
        ctx.add_message({"role": "user", "content": "Say 'one'."})
        await svc.process_frame(LLMContextFrame(context=ctx), FrameDirection.DOWNSTREAM)
        # Manually clear the sticky-turn gate between turns since we're not
        # running through a real pipeline that would emit LLMFullResponseEndFrame.
        svc._turn_in_flight = False
        svc._turn_delegate = None

    # Flip to zai for turn 3.
    svc._provider_file.write_text("zai")
    # Bump mtime so _sync_provider re-reads.
    stat = svc._provider_file.stat()
    os.utime(svc._provider_file, (stat.st_atime, stat.st_mtime + 5))

    ctx = LLMContext()
    ctx.add_message({"role": "user", "content": "Say 'three'."})
    await svc.process_frame(LLMContextFrame(context=ctx), FrameDirection.DOWNSTREAM)

    assert or_calls == 2, f"expected 2 OR calls, got {or_calls}"
    assert zai_calls == 1, f"expected 1 zai call, got {zai_calls}"

    # Final LLMTextFrame in the spy should have come after the zai call.
    text_frames = [f for f in spy.captured if isinstance(f, LLMTextFrame)]
    assert text_frames, "expected at least one LLMTextFrame across all turns"
