"""Tests for daemon_control — self-restart helpers and bash safeguard."""
from __future__ import annotations

import asyncio
import os
import signal
from unittest.mock import patch

import pytest

from src import daemon_control


# ---------------------------------------------------------------------------
# is_dangerous_self_command — bash safeguard pattern detection


@pytest.mark.parametrize(
    "cmd",
    [
        "make restart",
        "make stop",
        " make  restart ",
        "hearectl restart",
        "hearectl stop",
        "./hearectl restart",
        "/Users/lenyk/myprojects/heare/hearectl stop",
        "kill 12345",
        "kill -9 12345",
        "kill -TERM 999",
        "pkill -f src.main",
        "pkill src.main",
        "killall python",
        "killall -9 Python",
    ],
)
def test_dangerous_command_detected(cmd: str) -> None:
    assert daemon_control.is_dangerous_self_command(cmd) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "make build",
        "make test",
        "ls -la",
        "echo hello",
        "git status",
        "make watch",
        "ps aux",  # plain ps — diagnostic, not destructive
        "kill -l",  # list signals — not a kill
        "killer-app --help",  # word-boundary safety
        "",
    ],
)
def test_benign_command_allowed(cmd: str) -> None:
    assert daemon_control.is_dangerous_self_command(cmd) is False


# ---------------------------------------------------------------------------
# schedule_self_exit — sends SIGTERM to current PID after delay


@pytest.mark.asyncio
async def test_schedule_self_exit_signals_current_pid() -> None:
    """The signal must target ``os.getpid()`` so the running daemon's
    own SIGTERM handler runs the graceful-shutdown path."""
    seen: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        seen.append((pid, sig))

    await daemon_control.schedule_self_exit(delay_s=0.0, _kill=fake_kill)
    assert seen == [(os.getpid(), signal.SIGTERM)]


@pytest.mark.asyncio
async def test_schedule_self_exit_respects_delay() -> None:
    """The delay parameter must actually defer the signal — TTS
    playback depends on this so the user hears the goodbye message."""
    fired_at: list[float] = []
    started = asyncio.get_event_loop().time()

    def fake_kill(pid: int, sig: int) -> None:
        fired_at.append(asyncio.get_event_loop().time() - started)

    await daemon_control.schedule_self_exit(delay_s=0.05, _kill=fake_kill)
    assert len(fired_at) == 1
    assert fired_at[0] >= 0.04, f"signal fired too early: {fired_at[0]}"


@pytest.mark.asyncio
async def test_schedule_self_exit_custom_signal() -> None:
    seen: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        seen.append(sig)

    await daemon_control.schedule_self_exit(
        delay_s=0.0, sig=signal.SIGINT, _kill=fake_kill,
    )
    assert seen == [signal.SIGINT]


# ---------------------------------------------------------------------------
# spawn_detached_respawn — detaches via setsid so child outlives parent


def test_spawn_detached_respawn_uses_start_new_session(tmp_path) -> None:
    """The child MUST be spawned with ``start_new_session=True``
    (POSIX ``setsid``) — otherwise it gets killed when the daemon
    dies and the whole point of the helper is lost."""
    fake_hearectl = tmp_path / "hearectl"
    fake_hearectl.write_text("#!/bin/sh\nexit 0\n")
    fake_hearectl.chmod(0o755)

    captured: dict = {}

    class _FakePopen:
        pid = 99999

        def __init__(self, *a, **kw):
            captured["args"] = a
            captured["kwargs"] = kw

    with patch.object(daemon_control, "_hearectl_path", return_value=fake_hearectl), \
         patch("src.daemon_control.subprocess.Popen", _FakePopen):
        pid = daemon_control.spawn_detached_respawn(delay_s=2.5)

    assert pid == 99999
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["close_fds"] is True
    # The shell command must reference the resolved hearectl path so we
    # don't pick up a different ``hearectl`` from PATH at exec time.
    cmd_string = captured["args"][0][2]
    assert str(fake_hearectl) in cmd_string
    assert "sleep 2.50" in cmd_string


def test_spawn_detached_respawn_raises_if_hearectl_missing(tmp_path) -> None:
    """If ``hearectl`` doesn't exist we MUST raise — never schedule a
    self-exit unless we know there's something to bring the daemon
    back up afterward."""
    missing = tmp_path / "no-such-launcher"
    with patch.object(daemon_control, "_hearectl_path", return_value=missing):
        with pytest.raises(FileNotFoundError):
            daemon_control.spawn_detached_respawn(delay_s=1.0)
