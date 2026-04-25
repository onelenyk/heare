"""Tests for IntentQueue + ActionWorker."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from src.actions import ActionWorker, Intent, IntentQueue


# -------- IntentQueue tests ----------------------------------------------


async def test_queue_submit_next_monotonic_id() -> None:
    q = IntentQueue()
    id1 = await q.submit({"tool": "bash", "args": "a"})
    id2 = await q.submit({"tool": "bash", "args": "b"})
    assert id1 == 1
    assert id2 == 2
    first = await q.next()
    second = await q.next()
    assert first.id == 1 and first.args == "a"
    assert second.id == 2 and second.args == "b"


async def test_queue_fifo_order() -> None:
    q = IntentQueue()
    for i, a in enumerate(["x", "y", "z"]):
        await q.submit({"tool": "bash", "args": a})
    ordered = [(await q.next()).args for _ in range(3)]
    assert ordered == ["x", "y", "z"]


async def test_cancel_latest_removes_newest() -> None:
    q = IntentQueue()
    await q.submit({"tool": "bash", "args": "a"})
    id2 = await q.submit({"tool": "bash", "args": "b"})
    cancelled = q.cancel_latest()
    assert cancelled is not None and cancelled.id == id2
    assert q.pending_count() == 1
    # next() returns the older intent
    first = await q.next()
    assert first.args == "a"


async def test_cancel_latest_on_empty_returns_none() -> None:
    q = IntentQueue()
    assert q.cancel_latest() is None


async def test_pending_count_tracks_mutations() -> None:
    q = IntentQueue()
    assert q.pending_count() == 0
    await q.submit({"tool": "bash", "args": "a"})
    await q.submit({"tool": "bash", "args": "b"})
    assert q.pending_count() == 2
    q.cancel_latest()
    assert q.pending_count() == 1
    await q.next()
    assert q.pending_count() == 0


def test_action_description_includes_summary_instruction() -> None:
    """Dispatch prompt must force Claude to emit a Ukrainian text summary.

    Terse prompts caused Claude to end the turn silently, leaking the
    ToolResultBlock into the next SDK call. Phase AH2-04: summary must be
    Ukrainian. Phase B-tools: tool name is normalized to the SDK's
    CamelCase identifier.
    """
    from src.actions import _action_description

    intent = Intent(id=1, tool="bash", args="echo hi", raw={})
    desc = _action_description(intent)
    # Contract: first line uses the SDK's CamelCase tool identifier.
    first_line = desc.split("\n", 1)[0]
    assert first_line == "Use the Bash tool: echo hi"
    # Must include a summarization directive so Claude responds with text.
    lowered = desc.lower()
    assert "after the tool completes" in lowered
    assert "reply" in lowered or "sentence" in lowered
    # Phase AH2-04: summary must be in Ukrainian.
    assert "ukrainian" in lowered or "українською" in desc


def test_action_description_normalizes_all_phase_b_tools() -> None:
    """Phase B-tools: each lowercase intent tool name is normalized to
    the SDK's CamelCase identifier in the dispatch prompt."""
    from src.actions import _action_description

    cases = [
        ("bash", "Bash"),
        ("read", "Read"),
        ("write", "Write"),
        ("edit", "Edit"),
        ("web_fetch", "WebFetch"),
        ("web_search", "WebSearch"),
    ]
    for intent_name, sdk_name in cases:
        intent = Intent(id=1, tool=intent_name, args="x", raw={})
        desc = _action_description(intent)
        first_line = desc.split("\n", 1)[0]
        assert first_line == f"Use the {sdk_name} tool: x", (
            f"{intent_name!r} → expected SDK name {sdk_name!r}, got {first_line!r}"
        )


def test_action_description_unknown_tool_passes_through() -> None:
    """Phase B-tools: unknown tool names pass through unchanged (the queue
    allowlist is responsible for validation, not this helper)."""
    from src.actions import _action_description

    intent = Intent(id=1, tool="mystery", args="x", raw={})
    desc = _action_description(intent)
    first_line = desc.split("\n", 1)[0]
    assert first_line == "Use the mystery tool: x"


async def test_submit_forwards_decision_and_transcript_ids() -> None:
    """US-B0-01: decision_id + transcript_id round-trip submit→next onto Intent."""
    q = IntentQueue()
    iid = await q.submit(
        {"tool": "bash", "args": "echo hi"},
        decision_id=42,
        transcript_id=7,
    )
    assert iid == 1
    intent = await q.next()
    assert intent.decision_id == 42
    assert intent.transcript_id == 7


async def test_submit_defaults_correlation_ids_to_none() -> None:
    """US-B0-01: omitting kwargs leaves decision_id / transcript_id as None."""
    q = IntentQueue()
    await q.submit({"tool": "bash", "args": "ls"})
    intent = await q.next()
    assert intent.decision_id is None
    assert intent.transcript_id is None


async def test_submit_accepts_all_phase_b_tools() -> None:
    """Phase B-tools US-BT-01: every documented tool keyword enqueues."""
    for tool in ("bash", "read", "write", "edit", "web_fetch", "web_search"):
        q = IntentQueue()
        result = await q.submit({"tool": tool, "args": "x"})
        assert result == 1, f"tool {tool!r} should have been accepted"
        assert q.pending_count() == 1


async def test_submit_rejects_tool_outside_expanded_allowlist(caplog) -> None:
    """Phase B-tools US-BT-01: tools not in the expanded allowlist still dropped."""
    q = IntentQueue()
    with caplog.at_level(logging.WARNING, logger="heare.actions"):
        result = await q.submit({"tool": "filesystem", "args": "/etc"})
    assert result is None
    assert q.pending_count() == 0
    assert any("tool not allowed" in r.message.lower() for r in caplog.records)


async def test_submit_rejects_tool_not_in_allowlist(caplog) -> None:
    """Phase B US-B-02: tool outside ALLOWED_TOOLS is dropped with warning."""
    q = IntentQueue()
    with caplog.at_level(logging.WARNING, logger="heare.actions"):
        result = await q.submit({"tool": "SuperCoolTool", "args": "whatever"})
    assert result is None
    assert q.pending_count() == 0
    assert any(
        "tool not allowed" in r.message.lower() for r in caplog.records
    ), f"expected 'tool not allowed' warning, got {[r.message for r in caplog.records]}"


async def test_submit_rejects_args_over_max_length(caplog) -> None:
    """Phase B US-B-02: args longer than MAX_ARGS_LEN dropped with warning."""
    from src.actions import MAX_ARGS_LEN

    q = IntentQueue()
    long_args = "x" * (MAX_ARGS_LEN + 1)
    with caplog.at_level(logging.WARNING, logger="heare.actions"):
        result = await q.submit({"tool": "bash", "args": long_args})
    assert result is None
    assert q.pending_count() == 0
    assert any(
        "args too long" in r.message.lower() for r in caplog.records
    ), f"expected 'args too long' warning, got {[r.message for r in caplog.records]}"


async def test_submit_accepts_bash_with_short_args() -> None:
    """Phase B US-B-02: the common path (tool='bash', short args) still works."""
    q = IntentQueue()
    result = await q.submit({"tool": "bash", "args": "echo hi"})
    assert result == 1
    assert q.pending_count() == 1


async def test_submit_beyond_max_pending_drops_and_logs(caplog) -> None:
    q = IntentQueue(max_pending=2)
    with caplog.at_level(logging.WARNING, logger="heare.actions"):
        id1 = await q.submit({"tool": "bash", "args": "a"})
        id2 = await q.submit({"tool": "bash", "args": "b"})
        dropped = await q.submit({"tool": "bash", "args": "c"})
    assert id1 == 1 and id2 == 2
    assert dropped is None
    assert q.pending_count() == 2
    assert any("queue full" in r.message.lower() for r in caplog.records)


# -------- ActionWorker tests ---------------------------------------------


def _make_worker(call_action_impl, timeout: float = 1.0, kill_impl=None):
    q = IntentQueue()
    backend = MagicMock()
    backend.call_action = call_action_impl
    if kill_impl is not None:
        backend.kill_running_action = kill_impl
    results: list[tuple[Intent, str]] = []
    errors: list[tuple[Intent, BaseException]] = []

    async def on_result(intent: Intent, summary: str, result: dict) -> None:
        results.append((intent, summary))

    async def on_error(intent: Intent, exc: BaseException) -> None:
        errors.append((intent, exc))

    worker = ActionWorker(q, backend, on_result, on_error, timeout=timeout)
    return q, worker, backend, results, errors


async def test_worker_happy_path_dispatch_shape() -> None:
    async def call_action(description: str):
        # Contract: first line is "Use the <SDK tool> tool: <args>". The
        # SDK tool name is CamelCase (Phase B-tools normalization).
        assert description.split("\n", 1)[0] == "Use the Edit tool: modify file"
        return {"summary": "ok"}

    q, worker, backend, results, errors = _make_worker(call_action)
    await q.submit({"tool": "edit", "args": "modify file"})
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)  # let worker process one iteration
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(results) == 1
    assert results[0][1] == "ok"
    assert errors == []


async def test_worker_exception_in_call_action_continues_loop() -> None:
    calls = {"n": 0}

    async def call_action(description: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"summary": "second-ok"}

    q, worker, backend, results, errors = _make_worker(call_action)
    await q.submit({"tool": "edit", "args": "first"})
    await q.submit({"tool": "edit", "args": "second"})
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(errors) == 1
    assert isinstance(errors[0][1], RuntimeError)
    assert len(results) == 1
    assert results[0][1] == "second-ok"


async def test_worker_timeout_triggers_on_error_and_kill_called() -> None:
    kill_called = {"n": 0}

    async def slow_call(description: str):
        await asyncio.sleep(5)
        return {"summary": "never"}

    async def kill():
        kill_called["n"] += 1
        return True

    q, worker, backend, results, errors = _make_worker(
        slow_call, timeout=0.05, kill_impl=kill
    )
    await q.submit({"tool": "edit", "args": "a"})
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(errors) == 1
    assert isinstance(errors[0][1], asyncio.TimeoutError)
    assert kill_called["n"] == 1
    assert results == []


async def test_worker_cancelled_error_propagates_cleanly() -> None:
    async def call_action(description: str):
        return {"summary": "ok"}

    q, worker, backend, results, errors = _make_worker(call_action)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.02)  # worker is blocked on queue.next()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_worker_on_result_raises_logged_and_loop_continues(caplog) -> None:
    calls = {"n": 0}

    async def call_action(description: str):
        calls["n"] += 1
        return {"summary": f"r{calls['n']}"}

    q = IntentQueue()
    backend = MagicMock()
    backend.call_action = call_action
    handled: list[str] = []

    async def bad_on_result(intent, summary, result):
        if len(handled) == 0:
            handled.append("first")
            raise RuntimeError("bad")
        handled.append("second")

    async def on_error(intent, exc):
        pass

    worker = ActionWorker(q, backend, bad_on_result, on_error, timeout=1.0)
    await q.submit({"tool": "edit", "args": "a"})
    await q.submit({"tool": "edit", "args": "b"})
    with caplog.at_level(logging.ERROR, logger="heare.actions"):
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert handled == ["first", "second"]


async def test_worker_on_error_raises_logged_and_loop_continues(caplog) -> None:
    async def call_action(description: str):
        raise RuntimeError("boom")

    q = IntentQueue()
    backend = MagicMock()
    backend.call_action = call_action
    on_error_calls = {"n": 0}

    async def on_result(intent, summary, result):
        pass

    async def bad_on_error(intent, exc):
        on_error_calls["n"] += 1
        if on_error_calls["n"] == 1:
            raise ValueError("bad handler")

    worker = ActionWorker(q, backend, on_result, bad_on_error, timeout=1.0)
    await q.submit({"tool": "edit", "args": "a"})
    await q.submit({"tool": "edit", "args": "b"})
    with caplog.at_level(logging.ERROR, logger="heare.actions"):
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert on_error_calls["n"] == 2


async def test_intent_language_defaults_to_en() -> None:
    """US-VFA-01: Intent.language defaults to 'en' when no kwarg is passed."""
    q = IntentQueue()
    await q.submit({"tool": "bash", "args": "echo x"})
    intent = await q.next()
    assert intent.language == "en"


async def test_submit_language_roundtrip() -> None:
    """US-VFA-01: language='uk' set via submit kwarg round-trips through next()."""
    q = IntentQueue()
    await q.submit({"tool": "bash", "args": "echo x"}, language="uk")
    intent = await q.next()
    assert intent.language == "uk"


async def test_worker_no_kill_method_is_no_op() -> None:
    """When backend doesn't implement kill_running_action, timeout still
    fires on_error without crashing."""

    async def slow_call(description: str):
        await asyncio.sleep(5)

    q = IntentQueue()
    backend = MagicMock(spec=["call_action"])  # no kill_running_action
    backend.call_action = slow_call

    errors: list = []

    async def on_result(intent, summary, result):
        pass

    async def on_error(intent, exc):
        errors.append(exc)

    worker = ActionWorker(q, backend, on_result, on_error, timeout=0.05)
    await q.submit({"tool": "edit", "args": "a"})
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(errors) == 1
    assert isinstance(errors[0], asyncio.TimeoutError)


# ============================================================================
# CCS-05a — IntentQueue.cancel_active
# ============================================================================


class _StubManager:
    """Minimal stand-in for ConversationManager.record_action_cancelled."""

    def __init__(self) -> None:
        self.cancelled: list[tuple[int, str, str]] = []

    def record_action_cancelled(self, intent_id: int, *, tool: str = "", args: str = "") -> None:
        self.cancelled.append((intent_id, tool, args))


async def test_intent_queue_cancel_active_drains_queue() -> None:
    """AC: cancel_active() drains the queue and records each intent cancelled."""
    q = IntentQueue()
    mgr = _StubManager()
    q.bind_conversation_manager(mgr)
    id1 = await q.submit({"tool": "bash", "args": "a"})
    id2 = await q.submit({"tool": "bash", "args": "b"})
    id3 = await q.submit({"tool": "bash", "args": "c"})
    assert q.pending_count() == 3

    cancelled = await q.cancel_active()
    assert cancelled is True
    assert q.pending_count() == 0
    cancelled_ids = sorted(t[0] for t in mgr.cancelled)
    assert cancelled_ids == sorted([id1, id2, id3])


async def test_intent_queue_cancel_active_empty_queue_no_op() -> None:
    """AC: empty queue + no in-flight → returns False, fires no indication."""
    from src.indication import (
        IndicationKind as _IK,
        Indication as _Ind,
        set_indication as _set_ind,
    )
    from src.config import IndicationSettings as _IS
    import datetime as _dt

    captured: list[_IK] = []

    class _RB:
        name = "visual"

        async def fire(self, kind, level, title, body, meta) -> None:
            captured.append(kind)

        async def aclose(self) -> None:
            pass

    outside_quiet = _dt.datetime(2026, 4, 24, 12, 0)
    facade = _Ind(_IS(), [_RB()], wallclock=lambda: outside_quiet)
    _set_ind(facade)
    try:
        q = IntentQueue()
        result = await q.cancel_active()
        assert result is False
        # Yield so any (erroneously) scheduled indication completes.
        await asyncio.sleep(0.05)
        assert _IK.INTENT_CANCELLED not in captured
    finally:
        _set_ind(None)


async def test_intent_queue_cancel_active_fires_indication_when_anything_cancelled() -> None:
    """AC: indication fires exactly once when at least one intent is drained."""
    from src.indication import (
        IndicationKind as _IK,
        Indication as _Ind,
        set_indication as _set_ind,
    )
    from src.config import IndicationSettings as _IS
    import datetime as _dt

    captured: list[_IK] = []

    class _RB:
        name = "visual"

        async def fire(self, kind, level, title, body, meta) -> None:
            captured.append(kind)

        async def aclose(self) -> None:
            pass

    outside_quiet = _dt.datetime(2026, 4, 24, 12, 0)
    facade = _Ind(_IS(), [_RB()], wallclock=lambda: outside_quiet)
    _set_ind(facade)
    try:
        q = IntentQueue()
        await q.submit({"tool": "bash", "args": "a"})
        result = await q.cancel_active()
        assert result is True
        for _ in range(10):
            await asyncio.sleep(0.02)
            if _IK.INTENT_CANCELLED in captured:
                break
        assert captured.count(_IK.INTENT_CANCELLED) == 1
    finally:
        _set_ind(None)


async def test_intent_queue_cancel_active_calls_worker_in_flight_stub() -> None:
    """AC: bound cancel_in_flight callback is invoked by cancel_active.

    Story 5b will provide the real kill semantics; for 5a the worker stub
    just needs to receive the call.
    """
    q = IntentQueue()
    calls: list[bool] = []

    async def _cancel_in_flight() -> bool:
        calls.append(True)
        return True

    q.bind_worker(_cancel_in_flight)
    # Empty queue + worker reports True for in-flight cancellation:
    # cancel_active should still return True and invoke the stub.
    result = await q.cancel_active()
    assert calls == [True]
    assert result is True

