"""Tests for ActionWorker routing logic — US-HYB-03.

Verifies that ActionWorker routes tools correctly:
- Simple tools (bash, read, write, web_fetch, web_search) → execute_direct
- Complex tools (edit, MCP tools) → claude_cli.call_action
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.actions import ActionWorker, IntentQueue, Intent


@pytest.fixture
def mock_backend():
    """Create a mock ClaudeBackend."""
    backend = MagicMock()
    backend.call_action = AsyncMock(return_value={"summary": "claude result"})
    return backend


@pytest.fixture
def results():
    """Track on_result calls."""
    return []


@pytest.fixture
def errors():
    """Track on_error calls."""
    return []


@pytest.fixture
def worker(mock_backend, results, errors):
    """Create an ActionWorker with mocked callbacks."""
    q = IntentQueue()

    async def on_result(intent: Intent, summary: str, result: dict) -> None:
        results.append((intent, summary))

    async def on_error(intent: Intent, exc: Exception) -> None:
        errors.append((intent, exc))

    return ActionWorker(q, mock_backend, on_result, on_error, timeout=1.0), q


# -------- Simple tool routing tests --------

async def test_simple_tool_routes_to_direct(worker):
    """Intent with tool='bash' routes to execute_direct, not call_action."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "direct output", "error": None}

        await q.submit({"tool": "bash", "args": "echo test"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # execute_direct was called, call_action was NOT
        mock_direct.assert_called_once_with("bash", "echo test", worker._settings)
        worker.claude_cli.call_action.assert_not_called()


async def test_read_tool_routes_to_direct(worker):
    """Intent with tool='read' routes to execute_direct."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "file content", "error": None}

        await q.submit({"tool": "read", "args": "/tmp/file.txt"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_direct.assert_called_once_with("read", "/tmp/file.txt", worker._settings)
        worker.claude_cli.call_action.assert_not_called()


async def test_write_tool_routes_to_direct(worker):
    """Intent with tool='write' routes to execute_direct."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "Written", "error": None}

        await q.submit({"tool": "write", "args": "file.txt: content"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_direct.assert_called_once_with("write", "file.txt: content", worker._settings)
        worker.claude_cli.call_action.assert_not_called()


async def test_web_fetch_routes_to_direct(worker):
    """Intent with tool='web_fetch' routes to execute_direct."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "<html>page</html>", "error": None}

        await q.submit({"tool": "web_fetch", "args": "https://example.com"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_direct.assert_called_once_with("web_fetch", "https://example.com", worker._settings)
        worker.claude_cli.call_action.assert_not_called()


async def test_web_search_routes_to_direct(worker):
    """Intent with tool='web_search' routes to execute_direct."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "- result: url", "error": None}

        await q.submit({"tool": "web_search", "args": "test query"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_direct.assert_called_once_with("web_search", "test query", worker._settings)
        worker.claude_cli.call_action.assert_not_called()


# -------- Complex tool routing tests --------

async def test_edit_tool_routes_to_claude(worker):
    """Intent with tool='edit' routes to claude_cli.call_action."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        await q.submit({"tool": "edit", "args": "modify file"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # call_action was called, execute_direct was NOT
        worker.claude_cli.call_action.assert_called_once()
        mock_direct.assert_not_called()


async def test_mcp_tool_routes_to_claude(worker):
    """Intent with tool='mcp__github__create_issue' routes to claude_cli.call_action."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        await q.submit({"tool": "mcp__github__create_issue", "args": "create issue"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        worker.claude_cli.call_action.assert_called_once()
        mock_direct.assert_not_called()


# -------- Direct execution result handling --------

async def test_direct_success_calls_on_result(worker, results):
    """execute_direct returns success=true → on_result callback invoked."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "hello world", "error": None}

        await q.submit({"tool": "bash", "args": "echo hello"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(results) == 1
    assert results[0][1] == "hello world"


async def test_direct_failure_calls_on_result(errors):
    """execute_direct returns success=False → on_result callback invoked (not on_error).

    Direct-tool failures route through on_result so the result dict — including
    its `spoken` key — reaches _resolve_spoken in main.py (VFA BLOCKING #1 fix).
    Pure Python exceptions (timeout, RuntimeError) still go to on_error.
    """
    from src.actions import ActionWorker, IntentQueue

    q = IntentQueue()
    results_full: list[tuple] = []

    async def on_result(intent, summary: str, result: dict) -> None:
        results_full.append((intent, summary, result))

    async def on_error(intent, exc: Exception) -> None:
        errors.append((intent, exc))

    backend = MagicMock()
    backend.call_action = AsyncMock(return_value={"summary": "unused"})
    worker = ActionWorker(q, backend, on_result, on_error, timeout=1.0)

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": False, "output": "", "error": "Command failed"}

        await q.submit({"tool": "bash", "args": "false"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Failure result is routed through on_result (not on_error) so spoken key is preserved
    assert len(errors) == 0, f"expected no errors, got {errors}"
    assert len(results_full) == 1
    intent, summary, result = results_full[0]
    assert result.get("success") is False
    assert "Command failed" in summary


async def test_direct_result_format(worker, results):
    """execute_direct dict with output='hello' → on_result receives 'hello' as summary."""
    worker, q = worker

    with patch("src.actions.execute_direct", new_callable=AsyncMock) as mock_direct:
        mock_direct.return_value = {"success": True, "output": "test output", "error": None}

        await q.submit({"tool": "read", "args": "file.txt"})

        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert results[0][1] == "test output"


# -------- Claude path unchanged --------

async def test_claude_path_unchanged(worker, results):
    """Intent with tool='edit' → existing Claude flow unchanged, summary from result['summary']."""
    worker, q = worker
    worker.claude_cli.call_action.return_value = {"summary": "edit complete"}

    await q.submit({"tool": "edit", "args": "modify file"})

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(results) == 1
    assert results[0][1] == "edit complete"
