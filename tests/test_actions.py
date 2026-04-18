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

    async def on_result(intent: Intent, summary: str) -> None:
        results.append((intent, summary))

    async def on_error(intent: Intent, exc: BaseException) -> None:
        errors.append((intent, exc))

    worker = ActionWorker(q, backend, on_result, on_error, timeout=timeout)
    return q, worker, backend, results, errors


async def test_worker_happy_path_dispatch_shape() -> None:
    async def call_action(description: str):
        assert description == "Use the bash tool: echo hi"
        return {"summary": "ok"}

    q, worker, backend, results, errors = _make_worker(call_action)
    await q.submit({"tool": "bash", "args": "echo hi"})
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
    await q.submit({"tool": "bash", "args": "first"})
    await q.submit({"tool": "bash", "args": "second"})
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
    await q.submit({"tool": "bash", "args": "a"})
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

    async def bad_on_result(intent, summary):
        if len(handled) == 0:
            handled.append("first")
            raise RuntimeError("bad")
        handled.append("second")

    async def on_error(intent, exc):
        pass

    worker = ActionWorker(q, backend, bad_on_result, on_error, timeout=1.0)
    await q.submit({"tool": "bash", "args": "a"})
    await q.submit({"tool": "bash", "args": "b"})
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

    async def on_result(intent, summary):
        pass

    async def bad_on_error(intent, exc):
        on_error_calls["n"] += 1
        if on_error_calls["n"] == 1:
            raise ValueError("bad handler")

    worker = ActionWorker(q, backend, on_result, bad_on_error, timeout=1.0)
    await q.submit({"tool": "bash", "args": "a"})
    await q.submit({"tool": "bash", "args": "b"})
    with caplog.at_level(logging.ERROR, logger="heare.actions"):
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    assert on_error_calls["n"] == 2


async def test_worker_no_kill_method_is_no_op() -> None:
    """When backend doesn't implement kill_running_action, timeout still
    fires on_error without crashing."""

    async def slow_call(description: str):
        await asyncio.sleep(5)

    q = IntentQueue()
    backend = MagicMock(spec=["call_action"])  # no kill_running_action
    backend.call_action = slow_call

    errors: list = []

    async def on_result(intent, summary):
        pass

    async def on_error(intent, exc):
        errors.append(exc)

    worker = ActionWorker(q, backend, on_result, on_error, timeout=0.05)
    await q.submit({"tool": "bash", "args": "a"})
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(errors) == 1
    assert isinstance(errors[0], asyncio.TimeoutError)
