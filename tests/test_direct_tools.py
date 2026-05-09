"""Tests for direct_tools module — US-HYB-02.

Comprehensive unit tests for each simple tool: bash, read, write, web_fetch, web_search.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.agent.tools.direct import (
    _is_simple_tool,
    execute_direct,
    _execute_bash,
    _execute_read,
    _execute_write,
    _execute_web_fetch,
    _execute_web_search,
    _execute_list_profiles,
    _execute_create_profile,
    _execute_rename_profile,
    _execute_delete_profile,
)


# -------- Fixtures --------

@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Create a Settings instance with a temporary workspace."""
    return Settings(workspace_dir=tmp_path)


def _skip_long_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make asyncio.sleep return immediately for delays >= 1s.

    Used by re_enroll tests so the 2.6s pre-recording countdown wait
    doesn't pay real time. Sub-second waits still run normally so any
    race-condition coverage they're providing is preserved.
    """
    real_sleep = asyncio.sleep

    async def _fast_sleep(delay: float, *args, **kwargs):
        if delay >= 1.0:
            return
        return await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)


# -------- _is_simple_tool tests (US-HYB-04 preview) --------

def test_bash_is_simple() -> None:
    assert _is_simple_tool("bash") is True


def test_read_is_simple() -> None:
    assert _is_simple_tool("read") is True


def test_write_is_simple() -> None:
    assert _is_simple_tool("write") is True


def test_web_fetch_is_simple() -> None:
    assert _is_simple_tool("web_fetch") is True


def test_web_search_is_simple() -> None:
    assert _is_simple_tool("web_search") is True


def test_edit_is_not_simple() -> None:
    assert _is_simple_tool("edit") is False


def test_mcp_tool_is_not_simple() -> None:
    assert _is_simple_tool("mcp__github__create_issue") is False


def test_mcp_filesystem_is_not_simple() -> None:
    assert _is_simple_tool("mcp__filesystem__read_file") is False


def test_unknown_tool_is_not_simple() -> None:
    assert _is_simple_tool("unknown") is False


# -------- execute_direct routing tests --------

async def test_execute_direct_routes_to_bash() -> None:
    with patch("src.agent.tools.direct._execute_bash", new_callable=AsyncMock) as mock_bash:
        mock_bash.return_value = {"success": True, "output": "hello", "error": None}
        result = await execute_direct("bash", "echo hello", None)
        assert result["success"] is True
        assert result["output"] == "hello"
        mock_bash.assert_called_once_with("echo hello", None)


async def test_execute_direct_routes_to_read() -> None:
    with patch("src.agent.tools.direct._execute_read", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = {"success": True, "output": "content", "error": None}
        result = await execute_direct("read", "/tmp/file.txt", None)
        assert result["success"] is True
        mock_read.assert_called_once_with("/tmp/file.txt", None)


async def test_execute_direct_unknown_tool() -> None:
    result = await execute_direct("unknown", "args", None)
    assert result["success"] is False
    assert "Unknown direct tool" in result["error"]


# -------- _execute_bash tests --------

async def test_bash_success() -> None:
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stdout = asyncio.StreamReader()
        mock_proc.stderr = asyncio.StreamReader()
        # Feed data to streams
        mock_proc.stdout.feed_data(b"hello world\n")
        mock_proc.stdout.feed_eof()
        mock_proc.stderr.feed_eof()

        async def mock_communicate():
            return b"hello world\n", b""

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("echo hello", None)
        assert result["success"] is True
        assert result["output"] == "hello world\n"
        assert result["error"] is None
        assert result["exit_code"] == 0


async def test_bash_timeout() -> None:
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("sleep 100", None)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()


async def test_bash_stderr() -> None:
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.stdout = asyncio.StreamReader()
        mock_proc.stderr = asyncio.StreamReader()

        async def mock_communicate():
            return b"", b"error message\n"

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("false", None)
        assert result["success"] is False
        assert result["error"] == "error message\n"
        assert result["exit_code"] == 1


async def test_bash_with_workspace_dir(settings: Settings) -> None:
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_communicate():
            return b"output", b""

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        await _execute_bash("ls", settings)
        # Verify cwd was passed
        mock_subprocess.assert_called_once()
        call_kwargs = mock_subprocess.call_args[1]
        assert "cwd" in call_kwargs
        assert call_kwargs["cwd"] == settings.workspace_dir


# -------- CCS-05b cancel/kill paths --------


async def test_bash_spawned_with_new_session_kwarg(settings: Settings) -> None:
    """AC#4: bash subprocess spawned with start_new_session=True so the
    process group can be signalled via os.killpg on cancel."""
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_communicate():
            return b"", b""

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        await _execute_bash("true", settings)
        mock_subprocess.assert_called_once()
        call_kwargs = mock_subprocess.call_args[1]
        assert call_kwargs.get("start_new_session") is True, (
            f"start_new_session=True must be passed to create_subprocess_shell; got {call_kwargs}"
        )


async def test_bash_kill_via_process_group_within_3s_no_orphans(settings: Settings) -> None:
    """AC#3 + AC#4: a real `bash -c 'sleep 60 & wait'` is killed within 3s
    of cancel; the entire process group is terminated so no orphan
    children remain.

    Strategy: spawn a real bash that backgrounds a child sleep, capture
    the PGID, cancel the asyncio task, then probe with kill(0, signal=0)
    to verify the children are gone.
    """
    import os

    # Use a portable shell command that creates a child with a known PID
    # we can probe. The trailing `wait` keeps bash alive while children run.
    # We capture children's PIDs by writing them to a tmp file.
    pid_file = settings.workspace_dir / "child_pids.txt"
    cmd = (
        f"sh -c 'sleep 60 & echo $! > {pid_file}; sleep 60 & echo $! >> {pid_file}; wait'"
    )

    task = asyncio.create_task(_execute_bash(cmd, settings))

    # Give bash time to fork the children and write their pids.
    for _ in range(30):
        await asyncio.sleep(0.1)
        if pid_file.exists() and pid_file.read_text().count("\n") >= 2:
            break

    assert pid_file.exists(), "child PIDs file was never written"
    child_pids = [int(line) for line in pid_file.read_text().split() if line.strip()]
    assert len(child_pids) >= 1, f"expected at least one child PID, got {child_pids}"

    t0 = asyncio.get_event_loop().time()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=3.5)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        pytest.fail("cancel did not finish within 3.5s")
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 3.5, f"cancel took {elapsed:.2f}s (>3s budget)"

    # Wait briefly for the OS to reap the killed children.
    await asyncio.sleep(0.2)

    # Probe each child PID — kill(pid, 0) raises ProcessLookupError when
    # the process is gone. Permission errors are also acceptable (means
    # we no longer own the pid; the process is effectively dead from our
    # perspective).
    orphans = []
    for pid in child_pids:
        try:
            os.kill(pid, 0)
            # If we got here, the process is still alive — orphan!
            orphans.append(pid)
        except (ProcessLookupError, PermissionError, OSError):
            # Expected: child was killed via the process group.
            pass
    assert not orphans, f"orphan child processes remain after cancel: {orphans}"


async def test_bash_cancel_swallows_processlookuperror_when_already_finished() -> None:
    """AC: cancel path tolerates ProcessLookupError gracefully when the
    process has already exited between communicate() raising and the
    killpg call."""
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.pid = 99999  # nonexistent PID
        mock_proc.returncode = 0

        async def mock_communicate():
            raise asyncio.CancelledError()

        mock_proc.communicate = mock_communicate

        async def mock_wait():
            return 0

        mock_proc.wait = mock_wait
        mock_subprocess.return_value = mock_proc

        # Patch os.killpg to raise ProcessLookupError — handler must swallow.
        with patch("src.agent.tools.direct.os.killpg", side_effect=ProcessLookupError):
            with pytest.raises(asyncio.CancelledError):
                await _execute_bash("sleep 1", None)


async def test_web_fetch_cancellation_propagates_within_1s() -> None:
    """AC#5: cancelling a slow web_fetch propagates CancelledError within 1s."""
    import httpx

    started = asyncio.Event()

    class _SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):  # noqa: D401
            started.set()
            await asyncio.sleep(60)
            return httpx.Response(200, content=b"never")  # pragma: no cover

    # Patch httpx.AsyncClient to use the slow transport.
    real_client_cls = httpx.AsyncClient

    class _SlowClient(real_client_cls):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = _SlowTransport()
            super().__init__(*args, **kwargs)

    with patch("src.agent.tools.direct.httpx.AsyncClient", _SlowClient):
        from src.agent.tools.direct import _execute_web_fetch

        task = asyncio.create_task(_execute_web_fetch("https://slow.test/x", None))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        t0 = asyncio.get_event_loop().time()
        task.cancel()
        with pytest.raises((asyncio.CancelledError, BaseException)):
            await asyncio.wait_for(task, timeout=1.5)
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 1.5, f"web_fetch cancel took {elapsed:.2f}s (>1s budget)"


# -------- _execute_read tests --------

async def test_read_success(tmp_path: Path) -> None:
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")

    result = await _execute_read(str(test_file), None)
    assert result["success"] is True
    assert result["output"] == "hello world"
    assert result["error"] is None


async def test_read_not_found() -> None:
    result = await _execute_read("/nonexistent/file.txt", None)
    assert result["success"] is False
    assert "File not found" in result["error"]


async def test_read_large_file(tmp_path: Path) -> None:
    # Create 200k file
    large_content = "x" * 200_000
    test_file = tmp_path / "large.txt"
    test_file.write_text(large_content)

    result = await _execute_read(str(test_file), None)
    assert result["success"] is True
    assert len(result["output"]) == 100_000 + len("\n... (truncated)")
    assert "... (truncated)" in result["output"]


async def test_read_workspace_relative(settings: Settings) -> None:
    # Create file in workspace
    test_file = settings.workspace_dir / "test.txt"
    test_file.write_text("workspace content")

    result = await _execute_read("test.txt", settings)
    assert result["success"] is True
    assert result["output"] == "workspace content"


async def test_read_with_colon_format(tmp_path: Path) -> None:
    # Support "path: content" format - should only read path part
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    result = await _execute_read(f"{test_file}: ignored extra", None)
    assert result["success"] is True
    assert result["output"] == "content"


# -------- _execute_write tests --------

async def test_write_success(tmp_path: Path) -> None:
    test_file = tmp_path / "new.txt"
    result = await _execute_write(f"{test_file}: hello world", None)

    assert result["success"] is True
    assert "Written to" in result["output"]
    assert test_file.read_text() == "hello world"


async def test_write_no_colon() -> None:
    result = await _execute_write("just_a_path", None)
    assert result["success"] is False
    assert "requires 'path: content' format" in result["error"]


async def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    nested_file = tmp_path / "deep" / "nested" / "file.txt"
    result = await _execute_write(f"{nested_file}: content", None)

    assert result["success"] is True
    assert nested_file.exists()
    assert nested_file.read_text() == "content"


async def test_write_workspace_relative(settings: Settings) -> None:
    result = await _execute_write("relative.txt: workspace content", settings)

    assert result["success"] is True
    expected_file = settings.workspace_dir / "relative.txt"
    assert expected_file.exists()
    assert expected_file.read_text() == "workspace content"


# -------- Path-traversal guard tests --------

async def test_read_rejects_path_outside_workspace(settings: Settings, tmp_path: Path) -> None:
    """An absolute path outside settings.workspace_dir must be rejected."""
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    result = await _execute_read(str(outside), settings)
    assert result["success"] is False
    assert "outside" in result["error"]


async def test_read_rejects_dot_dot_escape(settings: Settings) -> None:
    """A relative path with `..` that escapes the workspace must be rejected."""
    result = await _execute_read("../../../etc/passwd", settings)
    assert result["success"] is False
    assert "outside" in result["error"]


async def test_write_rejects_path_outside_workspace(settings: Settings, tmp_path: Path) -> None:
    # Distinct filename so we don't collide with leftover data from sibling tests.
    outside = tmp_path.parent / "write_should_not_create.txt"
    if outside.exists():
        outside.unlink()
    result = await _execute_write(f"{outside}: hostile", settings)
    assert result["success"] is False
    assert "outside" in result["error"]
    assert not outside.exists()


# -------- _execute_web_fetch tests --------

async def test_web_fetch_success() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>content</html>"
    mock_response.content = b"<html>content</html>"

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_fetch("https://example.com", None)
        assert result["success"] is True
        assert "<html>content</html>" in result["output"]


async def test_web_fetch_binary() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = None  # No text property
    mock_response.content = b"\x00\x01\x02binary data"

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_fetch("https://example.com/binary", None)
        assert result["success"] is True
        assert "[Binary response," in result["output"]


async def test_web_fetch_large(tmp_path: Path) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    large_content = "x" * 100_000
    mock_response.text = large_content
    mock_response.content = large_content.encode()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_fetch("https://example.com/large", None)
        assert result["success"] is True
        assert len(result["output"]) == 50_000 + len("\n... (truncated)")


async def test_web_fetch_http_error() -> None:
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.reason_phrase = "Not Found"

    with patch("httpx.AsyncClient") as mock_client:
        # Make raise_for_status actually raise an exception
        async def mock_get(*args, **kwargs):
            class MockResponse:
                status_code = 404
                reason_phrase = "Not Found"

                def raise_for_status(self):
                    raise httpx.HTTPStatusError("Not Found", request=MagicMock(), response=self)

                @property
                def text(self):
                    return ""

                @property
                def content(self):
                    return b""

            return MockResponse()

        mock_client.return_value.__aenter__.return_value.get = mock_get

        result = await _execute_web_fetch("https://example.com/missing", None)
        assert result["success"] is False
        assert "404" in result["error"] or "Not Found" in result["error"]


async def test_web_fetch_invalid_url() -> None:
    result = await _execute_web_fetch("not-a-url", None)
    assert result["success"] is False
    assert "must start with" in result["error"]


# -------- _execute_web_search tests (DuckDuckGo) --------

async def test_web_search_duckduckgo_success() -> None:
    import httpx

    html = (
        '<a class="result__a" href="https://example.com/1">Result 1</a>'
        '<a class="result__a" href="https://example.com/2">Result 2</a>'
    )

    # Create a real httpx Response with our test HTML
    mock_response = httpx.Response(
        200,
        text=html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    # Create a Settings object that forces DuckDuckGo (ignoring SERPER_API_KEY)
    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_search("test query", settings)
        assert result["success"] is True
        assert "Result 1" in result["output"]
        assert "https://example.com/1" in result["output"]


async def test_web_search_duckduckgo_includes_snippets() -> None:
    """DuckDuckGo result HTML with `result__snippet` lands in `output`."""
    import httpx

    html = (
        '<a class="result__a" href="https://example.com/1">Chili Recipe</a>'
        '<a class="result__snippet" href="https://example.com/1">'
        'Brown beef, add tomatoes and beans, simmer 20 minutes.</a>'
        '<a class="result__a" href="https://example.com/2">Other Page</a>'
    )
    mock_response = httpx.Response(
        200,
        text=html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )
        result = await _execute_web_search("chili recipe", settings)

    assert result["success"] is True
    output = result["output"]
    assert "Chili Recipe" in output
    assert "Brown beef" in output
    assert "simmer 20 minutes" in output
    assert "https://example.com/1" in output


async def test_web_search_serper_includes_snippets() -> None:
    """Serper organic snippets land in `output` when present."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={
        "organic": [
            {
                "title": "Chili con carne",
                "link": "https://example.com/recipe",
                "snippet": "Brown 1 lb beef with onion; add chili powder, cumin, tomatoes, beans; simmer.",
            },
            {
                "title": "Wikipedia",
                "link": "https://en.wikipedia.org/wiki/Chili_con_carne",
                "snippet": "Chili con carne is a spicy stew.",
            },
        ],
    })
    mock_response.raise_for_status = MagicMock()

    with patch.dict("os.environ", {"SERPER_API_KEY": "test-key"}, clear=False):
        with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await _execute_web_search("chili con carne", None)

    assert result["success"] is True
    output = result["output"]
    assert "Chili con carne" in output
    assert "Brown 1 lb beef" in output
    assert "spicy stew" in output
    assert "https://example.com/recipe" in output


async def test_web_search_network_failure() -> None:
    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=Exception("Network error")
        )

        result = await _execute_web_search("test", settings)
        assert result["success"] is False
        assert "DuckDuckGo search failed" in result["error"]


# -------- _execute_re_enroll: event-loop-must-not-block regression --------

async def test_re_enroll_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-enrollment must run sd.wait() off the event loop.

    Before the fix, _execute_re_enroll called sd.wait() synchronously inside
    an async function, blocking the daemon event loop for the entire recording
    window. This caused queued TTS frames, indication backend writes, and the
    edge-tts WSS warmup to stall until recording finished. The fix wraps the
    blocking sd.rec/sd.wait pair in asyncio.to_thread.

    This test mocks sounddevice so sd.wait blocks for ~300ms and asserts a
    parallel ticker coroutine continues to run during that window. With the
    fix the ticker increments multiple times; without it (sync sd.wait on
    the main loop) the ticker would not increment until recording ended.
    """
    import sys
    import time as _time

    import numpy as _np

    from src.voice.indication import core as indication_mod
    from src.voice.speaker import gallery as gallery_mod
    from src.voice.speaker import id as speaker_id_mod
    from src import config as config_mod
    from src.agent.tools.direct import _execute_re_enroll
    from src.voice.indication.core import IndicationKind

    _skip_long_sleeps(monkeypatch)

    # Fake sounddevice with a blocking wait
    fake_sd = MagicMock()
    fake_sd.rec.return_value = _np.zeros((16000, 1), dtype="int16")
    fake_sd.wait.side_effect = lambda: _time.sleep(0.3)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    # Fake speaker_id model + embed
    monkeypatch.setattr(speaker_id_mod, "load_model", lambda: object())
    monkeypatch.setattr(
        speaker_id_mod,
        "embed",
        lambda pcm, sr, model: _np.zeros(192, dtype=_np.float32),
    )

    # Fake SpeakerGallery
    fake_gallery = MagicMock()
    fake_gallery.get_label.return_value = "owner"
    fake_gallery.enroll_owner = MagicMock()
    monkeypatch.setattr(
        gallery_mod.SpeakerGallery,
        "load",
        staticmethod(lambda f: fake_gallery),
    )

    # Fake load_settings
    fake_settings = MagicMock()
    fake_settings.speakers_file = tmp_path / "speakers.json"
    monkeypatch.setattr(config_mod, "load_settings", lambda: fake_settings)

    # Indication recorder captures notify(kind, **kwargs) with monotonic ts
    notify_log: list[tuple[float, IndicationKind, dict]] = []
    fake_ind = MagicMock()
    fake_ind.notify.side_effect = lambda kind, **kw: notify_log.append(
        (_time.monotonic(), kind, kw)
    )
    monkeypatch.setattr(indication_mod, "get_indication", lambda: fake_ind)

    # Concurrent ticker — should advance during the 0.3s blocking window
    tick_count = 0

    async def ticker() -> None:
        nonlocal tick_count
        for _ in range(20):
            await asyncio.sleep(0.05)
            tick_count += 1

    counter_task = asyncio.create_task(ticker())
    try:
        result = await _execute_re_enroll("5", settings=None)
    finally:
        counter_task.cancel()
        try:
            await counter_task
        except asyncio.CancelledError:
            pass

    assert result["success"] is True, result
    # 0.3s blocking window / 0.05s tick interval ≈ 6 ticks. Require ≥ 3 so
    # transient scheduling jitter does not flake. Without the fix the loop
    # is fully blocked and tick_count would be 0–1.
    assert tick_count >= 3, (
        f"event loop appears blocked during recording (tick_count={tick_count})"
    )
    # Indication brackets fired in the right order
    kinds_logged = [k for _, k, _ in notify_log]
    assert IndicationKind.REENROLL_RECORDING_START in kinds_logged
    assert IndicationKind.REENROLL_RECORDING_END in kinds_logged
    start_idx = kinds_logged.index(IndicationKind.REENROLL_RECORDING_START)
    end_idx = kinds_logged.index(IndicationKind.REENROLL_RECORDING_END)
    assert start_idx < end_idx, "START must fire before END"
    # The two timestamps should bracket the recording window (>= ~0.3s apart)
    start_ts = notify_log[start_idx][0]
    end_ts = notify_log[end_idx][0]
    assert (end_ts - start_ts) >= 0.25, (
        f"START/END indication brackets are not separated by the recording "
        f"window — gap={end_ts - start_ts:.3f}s (regression to the original bug)"
    )


async def test_re_enroll_blocks_concurrent_transcription(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """While re_enroll is recording, is_enrollment_active must be True so the
    Generator and SpeakerTagger drop concurrent transcripts.

    Without this gate, the user's enrollment audio gets re-transcribed by STT,
    fed into the generator, and the generator emits a duplicate re_enroll
    intent — observed in production daemon.log on 2026-04-25 13:24, two
    back-to-back recordings from a single user request.

    This test uses the real Indication facade with no backends so the flag
    actually flips on notify(START)/notify(END). The recording is mocked so
    sd.wait blocks for ~0.4s. We sample the flag at three checkpoints:
    before START, mid-recording, and after END.
    """
    import sys
    import time as _time

    import numpy as _np

    from src.voice.speaker import gallery as gallery_mod
    from src.voice.speaker import id as speaker_id_mod
    from src import config as config_mod
    from src.agent.tools.direct import _execute_re_enroll
    from src.voice.indication.core import (
        Indication,
        IndicationSettings,
        set_indication,
    )

    # Real Indication facade with empty backend list — notify still flips
    # the enrollment-active flag but no audio/visual cue dispatch happens.
    real_ind = Indication(
        IndicationSettings(enabled=True, cooldown_seconds=0.0),
        backends=[],
    )
    set_indication(real_ind)

    # Snapshot the flag at well-defined moments. The recording mock blocks
    # for 0.4s so we have time to sample mid-window.
    flag_samples: dict[str, bool] = {}

    _skip_long_sleeps(monkeypatch)

    fake_sd = MagicMock()
    fake_sd.rec.return_value = _np.zeros((16000, 1), dtype="int16")

    def _blocking_wait() -> None:
        # Sample the flag inside the blocking wait — this proves the flag is
        # True for the entire window, not just at a single instant. We sleep
        # in two halves so the sampler has time to run if it wanted to.
        _time.sleep(0.2)
        flag_samples["mid_recording"] = real_ind.is_enrollment_active
        _time.sleep(0.2)

    fake_sd.wait.side_effect = _blocking_wait
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    monkeypatch.setattr(speaker_id_mod, "load_model", lambda: object())
    monkeypatch.setattr(
        speaker_id_mod,
        "embed",
        lambda pcm, sr, model: _np.zeros(192, dtype=_np.float32),
    )

    fake_gallery = MagicMock()
    fake_gallery.get_label.return_value = "owner"
    fake_gallery.enroll_owner = MagicMock()
    monkeypatch.setattr(
        gallery_mod.SpeakerGallery,
        "load",
        staticmethod(lambda f: fake_gallery),
    )

    fake_settings = MagicMock()
    fake_settings.speakers_file = tmp_path / "speakers.json"
    monkeypatch.setattr(config_mod, "load_settings", lambda: fake_settings)

    # Sample the flag before — should be False
    flag_samples["before"] = real_ind.is_enrollment_active

    try:
        result = await _execute_re_enroll("5", settings=None)
    finally:
        # Sample the flag after — should be False again
        flag_samples["after"] = real_ind.is_enrollment_active
        # Tear down the global facade so other tests start clean
        set_indication(None)

    assert result["success"] is True, result
    assert flag_samples["before"] is False, (
        "is_enrollment_active should be False before re_enroll starts"
    )
    assert flag_samples["mid_recording"] is True, (
        "is_enrollment_active must be True during the recording window so "
        "Generator and SpeakerTagger drop concurrent transcripts"
    )
    assert flag_samples["after"] is False, (
        "is_enrollment_active should be False once recording ends"
    )

    # Spot-check that the GeneratorProcessor's helper would honor the flag
    # during the window (we cannot construct a full GeneratorProcessor here
    # without pipecat fixtures, but the helper is a thin lazy-import — we
    # can mimic it via the same get_indication() call).
    from src.voice.indication.core import get_indication

    set_indication(real_ind)
    real_ind._enrollment_active = True
    try:
        ind = get_indication()
        assert ind is not None
        assert ind.is_enrollment_active is True, (
            "get_indication().is_enrollment_active must report True so the "
            "generator/tagger gate fires during enrollment"
        )
    finally:
        real_ind._enrollment_active = False
        set_indication(None)


async def test_re_enroll_plays_countdown_before_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The 3-2-1 countdown cue must fire BEFORE the recording-start cue
    so the user hears the countdown clearly through the speaker before
    the mic opens.
    """
    import sys
    import time as _time

    import numpy as _np

    from src.voice.speaker import gallery as gallery_mod
    from src.voice.speaker import id as speaker_id_mod
    from src import config as config_mod
    from src.agent.tools.direct import _execute_re_enroll
    from src.voice.indication.core import (
        Indication,
        IndicationKind,
        IndicationSettings,
        set_indication,
    )

    # Capture notify call order on a real Indication facade (no backends).
    real_ind = Indication(
        IndicationSettings(enabled=True, cooldown_seconds=0.0),
        backends=[],
    )
    set_indication(real_ind)

    notify_order: list[IndicationKind] = []
    original_notify = real_ind.notify

    def _capture(kind, **kw):
        notify_order.append(kind)
        return original_notify(kind, **kw)

    monkeypatch.setattr(real_ind, "notify", _capture)

    _skip_long_sleeps(monkeypatch)

    # Standard sounddevice + speaker_id + gallery + config mocks
    fake_sd = MagicMock()
    fake_sd.rec.return_value = _np.zeros((16000, 1), dtype="int16")
    fake_sd.wait.side_effect = lambda: _time.sleep(0.05)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    monkeypatch.setattr(speaker_id_mod, "load_model", lambda: object())
    monkeypatch.setattr(
        speaker_id_mod,
        "embed",
        lambda pcm, sr, model: _np.zeros(192, dtype=_np.float32),
    )

    fake_gallery = MagicMock()
    fake_gallery.get_label.return_value = "owner"
    fake_gallery.enroll_owner = MagicMock()
    monkeypatch.setattr(
        gallery_mod.SpeakerGallery,
        "load",
        staticmethod(lambda f: fake_gallery),
    )

    fake_settings = MagicMock()
    fake_settings.speakers_file = tmp_path / "speakers.json"
    monkeypatch.setattr(config_mod, "load_settings", lambda: fake_settings)

    try:
        result = await _execute_re_enroll("5", settings=None)
    finally:
        set_indication(None)

    assert result["success"] is True, result
    # The countdown must come BEFORE the start cue
    countdown_kind = getattr(IndicationKind, "REENROLL_COUNTDOWN", None)
    assert countdown_kind is not None, (
        "IndicationKind.REENROLL_COUNTDOWN is not defined — US-CD-1 must land first"
    )
    assert countdown_kind in notify_order, (
        f"REENROLL_COUNTDOWN was never notified; saw: {notify_order}"
    )
    assert IndicationKind.REENROLL_RECORDING_START in notify_order
    countdown_idx = notify_order.index(countdown_kind)
    start_idx = notify_order.index(IndicationKind.REENROLL_RECORDING_START)
    assert countdown_idx < start_idx, (
        f"REENROLL_COUNTDOWN must fire BEFORE REENROLL_RECORDING_START; "
        f"got order: {notify_order}"
    )


# -------- Voice profile management tests (US-VP-3 to US-VP-6) --------


@pytest.mark.asyncio
async def test_list_profiles_show_table(settings: Settings) -> None:
    """list_profiles returns a formatted table of all speakers."""
    from src.voice.speaker.gallery import SpeakerGallery
    from unittest.mock import patch

    # Mock gallery with 2 speakers
    mock_gallery = MagicMock(spec=SpeakerGallery)
    mock_gallery.list_speakers.return_value = ["owner", "guest_01"]
    mock_gallery.get_entry.side_effect = [
        {
            "label": "Owner",
            "embeddings": [[0.1, 0.2], [0.3, 0.4]],
            "created_at": "2025-01-15T10:30:00Z",
        },
        {
            "label": "Guest 01",
            "embeddings": [[0.5, 0.6]],
            "created_at": "2025-01-16T14:20:00Z",
        },
    ]

    with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
        result = await _execute_list_profiles("", settings)

    assert result["success"] is True
    output = result["output"]
    assert "ID       | Label      | Refs | Created" in output
    assert "---------+------------+------+---------" in output
    assert "owner" in output
    assert "2" in output  # owner has 2 refs
    assert "2025-01-15" in output
    assert "guest_01" in output
    assert "1" in output  # guest_01 has 1 ref
    # spoken field: two profiles
    spoken = result["spoken"]
    assert "2 profiles" in spoken["en"]
    assert "Owner" in spoken["en"] and "Guest 01" in spoken["en"]
    assert "uk" in spoken and "ru" in spoken


@pytest.mark.asyncio
async def test_list_profiles_empty(settings: Settings) -> None:
    """list_profiles returns a message when no profiles exist."""
    from src.voice.speaker.gallery import SpeakerGallery
    from unittest.mock import patch

    mock_gallery = MagicMock(spec=SpeakerGallery)
    mock_gallery.list_speakers.return_value = []

    with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
        result = await _execute_list_profiles("", settings)

    assert result["success"] is True
    assert "No voice profiles found" in result["output"]
    # spoken field for empty case
    assert result["spoken"]["en"] == "No voice profiles yet."
    assert result["spoken"]["uk"] == "Профілів поки немає."
    assert result["spoken"]["ru"] == "Профилей пока нет."


@pytest.mark.asyncio
async def test_create_profile_placeholder(settings: Settings) -> None:
    """create_profile creates a placeholder guest profile with empty embeddings."""
    from src.voice.speaker.gallery import SpeakerGallery
    from src.voice.indication.core import set_indication, Indication, IndicationSettings

    # Use a real Indication with no backends to test notification
    ind = Indication(IndicationSettings(enabled=False, cooldown_seconds=0.0), backends=[])
    set_indication(ind)
    try:
        mock_gallery = MagicMock(spec=SpeakerGallery)
        mock_gallery._speakers = {}
        mock_gallery.save = MagicMock()

        with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
            result = await _execute_create_profile("Alice", settings)

        assert result["success"] is True
        assert "guest_01" in result["output"]
        assert "placeholder" in result["output"].lower()

        # Verify profile was created with empty embeddings
        assert "guest_01" in mock_gallery._speakers
        assert mock_gallery._speakers["guest_01"]["label"] == "Alice"
        assert mock_gallery._speakers["guest_01"]["embeddings"] == []
        assert mock_gallery.save.called

        # spoken field: success path
        assert result["spoken"]["en"] == "Created profile Alice."
        assert result["spoken"]["uk"] == "Створив профіль Alice."
        assert result["spoken"]["ru"] == "Создал профиль Alice."
    finally:
        set_indication(None)


@pytest.mark.asyncio
async def test_create_profile_max_guests(settings: Settings) -> None:
    """create_profile returns error when 99 guests already exist."""
    from src.voice.speaker.gallery import SpeakerGallery
    from unittest.mock import patch

    mock_gallery = MagicMock(spec=SpeakerGallery)
    # Simulate 99 existing guests
    mock_gallery._speakers = {f"guest_{i:02d}": {} for i in range(1, 100)}

    with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
        result = await _execute_create_profile("Alice", settings)

    assert result["success"] is False
    assert "Max 99" in result["error"]
    # spoken field: error path
    assert result["spoken"]["en"] == "Cannot create more than 99 guest profiles."
    assert "uk" in result["spoken"]
    assert "ru" in result["spoken"]


@pytest.mark.asyncio
async def test_rename_profile_success(settings: Settings) -> None:
    """rename_profile successfully renames a speaker."""
    from src.voice.speaker.gallery import SpeakerGallery
    from src.voice.indication.core import set_indication, Indication, IndicationSettings
    from unittest.mock import patch

    ind = Indication(IndicationSettings(enabled=False, cooldown_seconds=0.0), backends=[])
    set_indication(ind)
    try:
        mock_gallery = MagicMock(spec=SpeakerGallery)
        mock_gallery.rename_speaker.return_value = True

        with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
            result = await _execute_rename_profile("guest_01 Alice", settings)

        assert result["success"] is True
        assert "renamed" in result["output"].lower() or "Alice" in result["output"]
        mock_gallery.rename_speaker.assert_called_once_with("guest_01", "Alice")
        # spoken field: rename success
        assert result["spoken"]["en"] == "Renamed guest_01 to Alice."
        assert result["spoken"]["uk"] == "Перейменував guest_01 на Alice."
        assert result["spoken"]["ru"] == "Переименовал guest_01 в Alice."
    finally:
        set_indication(None)


@pytest.mark.asyncio
async def test_rename_profile_not_found(settings: Settings) -> None:
    """rename_profile returns error when speaker not found."""
    from src.voice.speaker.gallery import SpeakerGallery
    from unittest.mock import patch

    mock_gallery = MagicMock(spec=SpeakerGallery)
    mock_gallery.rename_speaker.return_value = False

    with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
        result = await _execute_rename_profile("unknown Bob", settings)

    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_rename_profile_usage_error(settings: Settings) -> None:
    """rename_profile returns usage error when args incomplete."""
    result = await _execute_rename_profile("only_id", settings)

    assert result["success"] is False
    assert "Usage" in result["error"]


@pytest.mark.asyncio
async def test_delete_profile_success(settings: Settings) -> None:
    """delete_profile successfully deletes a guest profile."""
    from src.voice.speaker.gallery import SpeakerGallery
    from src.voice.indication.core import set_indication, Indication, IndicationSettings
    from unittest.mock import patch

    ind = Indication(IndicationSettings(enabled=False, cooldown_seconds=0.0), backends=[])
    set_indication(ind)
    try:
        mock_gallery = MagicMock(spec=SpeakerGallery)
        mock_gallery.remove_speaker.return_value = True

        with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
            result = await _execute_delete_profile("guest_01", settings)

        assert result["success"] is True
        assert "deleted" in result["output"].lower() or "guest_01" in result["output"]
        mock_gallery.remove_speaker.assert_called_once_with("guest_01")
        # spoken field: delete success
        assert result["spoken"]["en"] == "Deleted profile guest_01."
        assert result["spoken"]["uk"] == "Видалив профіль guest_01."
        assert result["spoken"]["ru"] == "Удалил профиль guest_01."
    finally:
        set_indication(None)


@pytest.mark.asyncio
async def test_delete_profile_owner_blocked(settings: Settings) -> None:
    """delete_profile blocks deletion of owner profile."""
    result = await _execute_delete_profile("owner", settings)

    assert result["success"] is False
    assert "Cannot delete owner" in result["error"]
    # spoken field: owner-block error
    assert result["spoken"]["en"] == "Cannot delete the owner profile."
    assert result["spoken"]["uk"] == "Не можу видалити профіль власника."
    assert result["spoken"]["ru"] == "Не могу удалить профиль владельца."


@pytest.mark.asyncio
async def test_delete_profile_not_found(settings: Settings) -> None:
    """delete_profile returns error when speaker not found."""
    from src.voice.speaker.gallery import SpeakerGallery
    from unittest.mock import patch

    mock_gallery = MagicMock(spec=SpeakerGallery)
    mock_gallery.remove_speaker.return_value = False

    with patch("src.voice.speaker.gallery.SpeakerGallery.load", return_value=mock_gallery):
        result = await _execute_delete_profile("unknown", settings)

    assert result["success"] is False
    assert "not found" in result["error"]


# -------- spoken field tests for re_enroll (US-VFA-02) --------


@pytest.mark.asyncio
async def test_re_enroll_spoken_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_execute_re_enroll success returns spoken dict with en/uk/ru keys."""
    import sys
    import time as _time

    import numpy as _np

    from src.voice.indication import core as indication_mod
    from src.voice.speaker import gallery as gallery_mod
    from src.voice.speaker import id as speaker_id_mod
    from src import config as config_mod
    from src.agent.tools.direct import _execute_re_enroll

    _skip_long_sleeps(monkeypatch)

    fake_sd = MagicMock()
    fake_sd.rec.return_value = _np.zeros((16000, 1), dtype="int16")
    fake_sd.wait.side_effect = lambda: _time.sleep(0.01)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    monkeypatch.setattr(speaker_id_mod, "load_model", lambda: object())
    monkeypatch.setattr(
        speaker_id_mod,
        "embed",
        lambda pcm, sr, model: _np.zeros(192, dtype=_np.float32),
    )

    fake_gallery = MagicMock()
    fake_gallery.get_label.return_value = "owner"
    fake_gallery.enroll_owner = MagicMock()
    monkeypatch.setattr(
        gallery_mod.SpeakerGallery,
        "load",
        staticmethod(lambda f: fake_gallery),
    )

    fake_settings = MagicMock()
    fake_settings.speakers_file = tmp_path / "speakers.json"
    monkeypatch.setattr(config_mod, "load_settings", lambda: fake_settings)

    fake_ind = MagicMock()
    monkeypatch.setattr(indication_mod, "get_indication", lambda: fake_ind)

    result = await _execute_re_enroll("5", settings=None)

    assert result["success"] is True
    spoken = result["spoken"]
    assert spoken["en"] == "Voice profile updated."
    assert spoken["uk"] == "Голосовий профіль оновлено."
    assert spoken["ru"] == "Голосовой профиль обновлён."


# -------- Spoken field tests for bash/read/write (US-VFA-03) --------


async def test_bash_spoken_short_stdout() -> None:
    """_execute_bash with short stdout includes it in spoken."""
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_communicate():
            return b"ok", b""

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("echo ok", None)
        assert result["success"] is True
        spoken = result["spoken"]
        assert spoken["en"] == "Done. ok"
        assert spoken["uk"] == "Виконано. ok"
        assert spoken["ru"] == "Готово. ok"


async def test_bash_spoken_long_stdout_no_leak() -> None:
    """_execute_bash with stdout > 80 chars uses plain 'Done.' — no raw output leak."""
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        long_out = "x" * 100

        async def mock_communicate():
            return long_out.encode(), b""

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("cat file", None)
        assert result["success"] is True
        spoken = result["spoken"]
        assert spoken["en"] == "Done."
        assert spoken["uk"] == "Виконано."
        assert spoken["ru"] == "Готово."
        # Crucially: raw output must NOT appear in spoken
        assert long_out not in spoken["en"]


async def test_bash_spoken_empty_stdout() -> None:
    """_execute_bash with empty stdout uses plain 'Done.'."""
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_communicate():
            return b"", b""

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("true", None)
        assert result["success"] is True
        assert result["spoken"]["en"] == "Done."


async def test_bash_spoken_error_path() -> None:
    """_execute_bash failure returns spoken without any raw stderr."""
    with patch("asyncio.create_subprocess_shell") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.returncode = 1

        async def mock_communicate():
            return b"", b"some detailed error message that should not appear"

        mock_proc.communicate = mock_communicate
        mock_subprocess.return_value = mock_proc

        result = await _execute_bash("false", None)
        assert result["success"] is False
        spoken = result["spoken"]
        assert spoken["en"] == "Command failed."
        assert spoken["uk"] == "Команда не виконалася."
        assert spoken["ru"] == "Команда не выполнилась."
        # Raw stderr must NOT leak into spoken
        assert "detailed error" not in spoken["en"]


async def test_read_spoken_success(tmp_path) -> None:
    """_execute_read returns spoken with line count on success."""
    test_file = tmp_path / "sample.txt"
    test_file.write_text("line one\nline two\nline three\n")

    result = await _execute_read(str(test_file), None)
    assert result["success"] is True
    spoken = result["spoken"]
    assert spoken["en"] == "Read 3 lines."
    assert spoken["uk"] == "Прочитав 3 рядків."
    assert spoken["ru"] == "Прочитал 3 строк."


async def test_read_spoken_file_not_found() -> None:
    """_execute_read returns spoken file-not-found phrase on missing file."""
    result = await _execute_read("/nonexistent/path/file.txt", None)
    assert result["success"] is False
    spoken = result["spoken"]
    assert spoken["en"] == "File not found."
    assert spoken["uk"] == "Файл не знайдено."
    assert spoken["ru"] == "Файл не найден."


async def test_write_spoken_success(tmp_path) -> None:
    """_execute_write returns spoken 'File saved.' on success."""
    test_file = tmp_path / "out.txt"
    result = await _execute_write(f"{test_file}: hello", None)
    assert result["success"] is True
    spoken = result["spoken"]
    assert spoken["en"] == "File saved."
    assert spoken["uk"] == "Файл збережено."
    assert spoken["ru"] == "Файл сохранён."


# -------- Spoken field tests for web_fetch/web_search (US-VFA-04) --------


async def test_web_fetch_spoken_success() -> None:
    """_execute_web_fetch success returns spoken 'Fetched. Want a summary?' in en/uk/ru."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>hello</html>"
    mock_response.content = b"<html>hello</html>"

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_fetch("https://example.com", None)

    assert result["success"] is True
    spoken = result["spoken"]
    assert spoken["en"] == "Fetched. Want a summary?"
    assert spoken["uk"] == "Завантажив. Зробити підсумок?"
    assert spoken["ru"] == "Загрузил. Сделать сводку?"
    # Raw HTML must NOT appear in spoken values
    assert "<html>" not in spoken["en"]


async def test_web_fetch_spoken_error() -> None:
    """_execute_web_fetch error returns spoken 'Fetch failed.' without raw details."""
    import httpx

    with patch("httpx.AsyncClient") as mock_client:
        async def mock_get(*args, **kwargs):
            class MockResp:
                status_code = 500
                reason_phrase = "Internal Server Error"

                def raise_for_status(self):
                    raise httpx.HTTPStatusError(
                        "Server error", request=MagicMock(), response=self
                    )

                @property
                def text(self):
                    return ""

                @property
                def content(self):
                    return b""

            return MockResp()

        mock_client.return_value.__aenter__.return_value.get = mock_get

        result = await _execute_web_fetch("https://example.com/fail", None)

    assert result["success"] is False
    spoken = result["spoken"]
    assert spoken["en"] == "Fetch failed."
    assert spoken["uk"] == "Не вдалося завантажити."
    assert spoken["ru"] == "Не удалось загрузить."


async def test_web_search_spoken_with_results() -> None:
    """_execute_web_search with N hits uses a minimal spoken cue — no result count, no titles."""
    import httpx

    html = (
        '<a class="result__a" href="https://example.com/1">Title One</a>'
        '<a class="result__a" href="https://example.com/2">Title Two</a>'
        '<a class="result__a" href="https://example.com/3">Title Three</a>'
    )
    mock_response = httpx.Response(
        200,
        text=html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_search("python asyncio", settings)

    assert result["success"] is True
    spoken = result["spoken"]
    # The hardcoded "Found N results. First: …" is gone — generator picks
    # the response from the snippets in `output` instead of the spoken cue.
    assert spoken["en"] == "Searching done."
    assert spoken["uk"] == "Пошук завершено."
    assert spoken["ru"] == "Поиск завершён."
    assert "Found" not in spoken["en"]
    assert "Title One" not in spoken["en"]


async def test_web_search_spoken_zero_results() -> None:
    """_execute_web_search with no hits returns spoken 'No results found.'."""
    import httpx

    mock_response = httpx.Response(
        200,
        text="<html>nothing here</html>",  # no result__a links
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

        result = await _execute_web_search("xyzzy42 nonexistent", settings)

    assert result["success"] is True
    spoken = result["spoken"]
    assert spoken["en"] == "No results found."
    assert spoken["uk"] == "Нічого не знайшов."
    assert spoken["ru"] == "Ничего не нашёл."


async def test_web_search_spoken_error() -> None:
    """_execute_web_search network failure returns spoken 'Search failed.'."""
    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=Exception("connection refused")
        )

        result = await _execute_web_search("anything", settings)

    assert result["success"] is False
    spoken = result["spoken"]
    assert spoken["en"] == "Search failed."
    assert spoken["uk"] == "Пошук не вдався."
    assert spoken["ru"] == "Поиск не удался."
    # Raw error details must NOT appear in spoken
    assert "connection refused" not in spoken["en"]


# -------- Chained top-page fetch (web_search_fetch_top) --------

def _ddg_html(*pairs: tuple[str, str]) -> str:
    return "".join(
        f'<a class="result__a" href="{url}">{title}</a>'
        for url, title in pairs
    )


async def test_web_search_chains_web_fetch_when_enabled(settings: Settings) -> None:
    """When web_search_fetch_top is True, the top URL is fetched and appended."""
    settings.web_search_fetch_top = True
    settings.web_search_provider = "duckduckgo"

    import httpx
    ddg_html = _ddg_html(
        ("https://example.com/recipe", "Chili Recipe"),
        ("https://example.com/other", "Other"),
    )
    ddg_response = httpx.Response(
        200,
        text=ddg_html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=ddg_response
        )

        async def fake_fetch(url, _settings):
            assert url == "https://example.com/recipe"
            return {
                "success": True,
                "output": "Brown beef. Add tomatoes. Simmer.",
                "error": None,
                "spoken": {"en": "Fetched. Want a summary?"},
            }

        with patch("src.agent.tools.direct._execute_web_fetch", new=fake_fetch):
            result = await _execute_web_search("chili recipe", settings)

    assert result["success"] is True
    output = result["output"]
    assert "Top page content:" in output
    assert "Brown beef" in output


async def test_web_search_skips_fetch_when_disabled(settings: Settings) -> None:
    """When web_search_fetch_top is False, no chained fetch happens."""
    settings.web_search_fetch_top = False
    settings.web_search_provider = "duckduckgo"

    import httpx
    ddg_html = _ddg_html(("https://example.com/x", "Title X"))
    ddg_response = httpx.Response(
        200,
        text=ddg_html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    fetch_calls: list[str] = []

    async def fake_fetch(url, _settings):
        fetch_calls.append(url)
        return {"success": True, "output": "should-not-appear"}

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=ddg_response
        )
        with patch("src.agent.tools.direct._execute_web_fetch", new=fake_fetch):
            result = await _execute_web_search("anything", settings)

    assert result["success"] is True
    assert fetch_calls == []
    assert "Top page content:" not in result["output"]
    assert "should-not-appear" not in result["output"]


async def test_web_search_swallows_top_fetch_error(settings: Settings) -> None:
    """A failing chained fetch must not break the search result."""
    settings.web_search_fetch_top = True
    settings.web_search_provider = "duckduckgo"

    import httpx
    ddg_html = _ddg_html(("https://example.com/x", "Title X"))
    ddg_response = httpx.Response(
        200,
        text=ddg_html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    async def failing_fetch(url, _settings):
        return {"success": False, "output": "", "error": "boom"}

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=ddg_response
        )
        with patch("src.agent.tools.direct._execute_web_fetch", new=failing_fetch):
            result = await _execute_web_search("anything", settings)

    assert result["success"] is True
    output = result["output"]
    assert "Title X" in output
    assert "Top page content:" not in output


# -------- CCS-02: numbered + structured search results --------

async def test_serper_returns_numbered_output_and_items() -> None:
    """Serper returns ``output`` numbered "1. … 2. …" AND a structured
    ``items`` list with contiguous ``n`` starting at 1 (knowledge_graph
    pushes organic numbering down by 0 since it gets ``n=0``)."""
    import re

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={
        "knowledgeGraph": {
            "title": "Chili",
            "description": "A spicy stew of meat, beans, and tomatoes.",
        },
        "organic": [
            {"title": "T1", "link": "https://e.com/1", "snippet": "S1."},
            {"title": "T2", "link": "https://e.com/2", "snippet": "S2."},
            {"title": "T3", "link": "https://e.com/3", "snippet": "S3."},
        ],
    })
    mock_response.raise_for_status = MagicMock()

    with patch.dict("os.environ", {"SERPER_API_KEY": "test-key"}, clear=False):
        with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await _execute_web_search("chili recipe", None)

    assert result["success"] is True
    output = result["output"]
    # Numbered organic results: 1.…\n\n2.…
    assert re.search(r"^.*\n\n1\.\s", output, re.DOTALL) or output.startswith("1. ") or "📚" in output
    assert re.search(r"\n\n1\.\s.+\n.+\n\n2\.\s", output, re.DOTALL), (
        f"expected numbered organic blocks 1./2., got: {output!r}"
    )

    items = result["items"]
    assert isinstance(items, list)
    assert len(items) == 4  # kg + 3 organic
    # Knowledge graph at position 0
    assert items[0]["n"] == 0
    # Organic items contiguous starting at 1
    organic = [it for it in items if it["n"] >= 1]
    ns = [it["n"] for it in organic]
    assert ns == [1, 2, 3]
    for it in organic:
        assert set(it.keys()) >= {"n", "title", "url", "snippet"}


async def test_duckduckgo_returns_numbered_output_and_items() -> None:
    """DuckDuckGo returns numbered output and ``items`` with ``n`` 1..N."""
    import re
    import httpx

    html = (
        '<a class="result__a" href="https://example.com/1">Title One</a>'
        '<a class="result__snippet" href="https://example.com/1">First snippet.</a>'
        '<a class="result__a" href="https://example.com/2">Title Two</a>'
        '<a class="result__snippet" href="https://example.com/2">Second snippet.</a>'
        '<a class="result__a" href="https://example.com/3">Title Three</a>'
        '<a class="result__snippet" href="https://example.com/3">Third snippet.</a>'
    )
    mock_response = httpx.Response(
        200,
        text=html,
        request=httpx.Request("GET", "https://html.duckduckgo.com/html/")
    )

    settings = Settings()
    settings.web_search_provider = "duckduckgo"

    with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )
        result = await _execute_web_search("test query", settings)

    assert result["success"] is True
    output = result["output"]
    assert output.startswith("1. ")
    assert re.search(r"^1\.\s.+\n.+\n.+\n\n2\.\s", output, re.DOTALL), (
        f"expected '1. …\\n\\n2. …' numbering, got: {output!r}"
    )
    items = result["items"]
    assert isinstance(items, list)
    assert len(items) == 3
    assert [it["n"] for it in items] == [1, 2, 3]
    for it in items:
        assert set(it.keys()) >= {"n", "title", "url", "snippet"}
    assert items[0]["title"] == "Title One"
    assert items[0]["snippet"] == "First snippet."


async def test_serper_knowledge_graph_at_position_0() -> None:
    """Serper knowledgeGraph entries land at items[0] with n=0 and
    kind='answer_box', and prefix the output with '📚 …'."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={
        "knowledgeGraph": {
            "title": "Pipecat",
            "description": "An open-source framework for voice agents.",
        },
        "organic": [
            {"title": "Repo", "link": "https://github.com/pipecat-ai", "snippet": "Source."},
        ],
    })
    mock_response.raise_for_status = MagicMock()

    with patch.dict("os.environ", {"SERPER_API_KEY": "test-key"}, clear=False):
        with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await _execute_web_search("pipecat", None)

    items = result["items"]
    assert items[0]["n"] == 0
    assert items[0]["kind"] == "answer_box"
    assert "Pipecat" in items[0]["title"]
    assert result["output"].startswith("📚 ")


async def test_search_serper_empty_results_has_empty_items() -> None:
    """Empty Serper response: spoken='No results found.', items=[]."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"organic": []})
    mock_response.raise_for_status = MagicMock()

    with patch.dict("os.environ", {"SERPER_API_KEY": "test-key"}, clear=False):
        with patch("src.agent.tools.direct.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await _execute_web_search("xyzzy", None)

    assert result["success"] is True
    assert result["items"] == []
    assert result["output"] == "No results found"
    assert result["spoken"]["en"] == "No results found."


async def test_search_duckduckgo_empty_results_has_empty_items() -> None:
    """Empty DuckDuckGo HTML: spoken='No results found.', items=[]."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html>nothing here</html>"
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )
        result = await _execute_web_search("xyzzy", None)

    assert result["success"] is True
    assert result["items"] == []
    assert result["output"] == "No results found"
    assert result["spoken"]["en"] == "No results found."
