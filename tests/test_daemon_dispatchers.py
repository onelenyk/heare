"""Tests for the LLM-facing dispatchers around daemon control.

Covers:
* ``_execute_stop_daemon`` / ``_execute_restart_daemon`` — consent gate,
  detach-then-exit ordering, error_code surfacing.
* ``_execute_bash`` self-target safeguard — refuses ``make restart``
  etc. so the LLM gets a clear signal to use the native tools.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.direct_tools import (
    _execute_bash,
    _execute_restart_daemon,
    _execute_stop_daemon,
)


# ---------------------------------------------------------------------------
# stop_daemon


@pytest.mark.asyncio
async def test_stop_daemon_refuses_without_user_confirmed() -> None:
    args = json.dumps({"user_confirmed": False})
    result = await _execute_stop_daemon(args)
    assert result["success"] is False
    assert result["error_code"] == "user_not_confirmed"


@pytest.mark.asyncio
async def test_stop_daemon_schedules_self_exit_when_confirmed() -> None:
    """Successful stop must schedule a SIGTERM to self via
    ``schedule_self_exit`` and return a spoken confirmation. The
    actual signal firing is mocked so the test process survives."""

    async def fake_schedule(*, delay_s: float, **_kwargs) -> None:
        # No-op — real impl would signal os.getpid() and kill the test runner.
        await asyncio.sleep(0)

    with patch("src.daemon_control.schedule_self_exit", AsyncMock(side_effect=fake_schedule)) as mock:
        args = json.dumps({"user_confirmed": True, "delay_s": 0.01})
        result = await _execute_stop_daemon(args)

    assert result["success"] is True
    assert "Shutting down" in result["spoken"]["en"]
    # Give the spawned task a chance to run.
    await asyncio.sleep(0.05)
    assert mock.await_count == 1
    assert mock.await_args.kwargs["delay_s"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_stop_daemon_rejects_invalid_json() -> None:
    result = await _execute_stop_daemon("not json{")
    assert result["success"] is False
    assert "Invalid JSON" in result["error"]


# ---------------------------------------------------------------------------
# restart_daemon


@pytest.mark.asyncio
async def test_restart_daemon_refuses_without_user_confirmed() -> None:
    args = json.dumps({"user_confirmed": False})
    result = await _execute_restart_daemon(args)
    assert result["success"] is False
    assert result["error_code"] == "user_not_confirmed"


@pytest.mark.asyncio
async def test_restart_daemon_spawns_respawner_then_schedules_exit() -> None:
    """Critical ordering: respawner spawn must happen BEFORE the
    self-exit task is scheduled, so a spawn failure cancels the
    restart entirely (we never kill the daemon if no respawn is
    queued)."""
    call_order: list[str] = []

    def fake_spawn(*, delay_s: float) -> int:
        call_order.append("spawn")
        return 12345

    async def fake_schedule(*, delay_s: float, **_kwargs) -> None:
        call_order.append("schedule_exit")
        await asyncio.sleep(0)

    with patch("src.daemon_control.spawn_detached_respawn", side_effect=fake_spawn) as spawn_mock, \
         patch("src.daemon_control.schedule_self_exit", AsyncMock(side_effect=fake_schedule)) as exit_mock:
        args = json.dumps({
            "user_confirmed": True,
            "self_exit_delay_s": 0.01,
            "respawn_delay_s": 0.02,
        })
        result = await _execute_restart_daemon(args)

    assert result["success"] is True
    assert "12345" in result["output"]
    assert spawn_mock.call_count == 1
    # Drain the create_task'd schedule_self_exit.
    await asyncio.sleep(0.05)
    assert exit_mock.await_count == 1
    # Spawn must come first — the test that matters most.
    assert call_order[0] == "spawn"


@pytest.mark.asyncio
async def test_restart_daemon_does_not_exit_if_respawn_fails() -> None:
    """If ``hearectl`` is missing, ``spawn_detached_respawn`` raises
    ``FileNotFoundError`` and we MUST NOT schedule a self-exit —
    otherwise we'd kill the daemon with no respawn lined up."""

    def fake_spawn(*, delay_s: float) -> int:
        raise FileNotFoundError("hearectl not found")

    schedule_mock = AsyncMock()

    with patch("src.daemon_control.spawn_detached_respawn", side_effect=fake_spawn), \
         patch("src.daemon_control.schedule_self_exit", schedule_mock):
        args = json.dumps({"user_confirmed": True})
        result = await _execute_restart_daemon(args)

    assert result["success"] is False
    assert result["error_code"] == "respawn_failed"
    assert "Couldn't find" in result["spoken"]["en"]
    # The decisive assertion — no self-exit scheduled when respawn failed.
    assert schedule_mock.await_count == 0
    assert schedule_mock.call_count == 0


@pytest.mark.asyncio
async def test_restart_daemon_respawn_delay_exceeds_self_exit_delay() -> None:
    """Default delays must satisfy ``respawn_delay_s > self_exit_delay_s``
    so the respawner doesn't try to ``hearectl start`` while this
    daemon's PID file still exists (cmd_start refuses with
    'already running')."""
    captured: dict = {}

    def fake_spawn(*, delay_s: float) -> int:
        captured["respawn_delay"] = delay_s
        return 11111

    schedule_mock = AsyncMock()

    with patch("src.daemon_control.spawn_detached_respawn", side_effect=fake_spawn), \
         patch("src.daemon_control.schedule_self_exit", schedule_mock):
        # No explicit overrides — exercise the defaults.
        args = json.dumps({"user_confirmed": True})
        await _execute_restart_daemon(args)
        await asyncio.sleep(0)

    assert schedule_mock.await_count == 1
    self_exit_delay = schedule_mock.await_args.kwargs["delay_s"]
    assert captured["respawn_delay"] > self_exit_delay


# ---------------------------------------------------------------------------
# bash safeguard


@pytest.mark.asyncio
async def test_bash_refuses_make_restart() -> None:
    """The reported regression: ``make restart`` shoots the daemon in
    the foot. Bash must refuse and tell the LLM to use the native tool."""
    result = await _execute_bash("make restart")
    assert result["success"] is False
    assert "self_targeted_restart" in result["error"]
    assert "restart_daemon" in result["error"]


@pytest.mark.asyncio
async def test_bash_refuses_hearectl_stop() -> None:
    result = await _execute_bash("./hearectl stop")
    assert result["success"] is False
    assert "self_targeted_restart" in result["error"]


@pytest.mark.asyncio
async def test_bash_refuses_kill_pid() -> None:
    result = await _execute_bash("kill -9 12345")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_bash_allows_benign_make_target(tmp_path) -> None:
    """``make build`` must still work — the guard is a precise pattern
    match, not a blanket ``make`` refusal."""
    from src.config import Settings

    settings = Settings(workspace_dir=tmp_path)
    # We want to verify the guard returns control flow to the actual
    # subprocess path, not that a real ``make build`` succeeds (which
    # would require a Makefile in tmp_path). Mock the subprocess call.
    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
        proc = mock_proc.return_value
        proc.communicate = AsyncMock(return_value=(b"hello", b""))
        proc.returncode = 0
        result = await _execute_bash("make build", settings)

    assert mock_proc.called, "guard should have allowed the command through"
    assert result["success"] is True
