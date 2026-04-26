"""US-IND-A4c: NotificationBackend — argv-only osascript, injection-safe."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.indication import IndicationKind, IndicationLevel
from src.indication_backends.notification import NotificationBackend


# ---------------------------------------------------------------------------
# build_command — pure helper (no subprocess)
# ---------------------------------------------------------------------------


def test_build_command_argv_length_2_when_no_sound() -> None:
    backend = NotificationBackend(force_enabled=True)
    cmd = backend.build_command(body="hi", title="t", sound_name="")
    # cmd = ["osascript", "-e", "on run argv", "-e", <line>, "-e", "end run", "--", body, title]
    assert cmd[0] == "osascript"
    assert cmd[-3:] == ["--", "hi", "t"]
    # Argv items after `--` are exactly 2 (body, title)
    sep = cmd.index("--")
    argv = cmd[sep + 1 :]
    assert len(argv) == 2
    # Display line must NOT include `sound name` clause
    display_line = cmd[4]
    assert "sound name" not in display_line


def test_build_command_argv_length_3_when_sound_name_present() -> None:
    backend = NotificationBackend(force_enabled=True)
    cmd = backend.build_command(body="hi", title="t", sound_name="Sosumi")
    assert cmd[-4:] == ["--", "hi", "t", "Sosumi"]
    sep = cmd.index("--")
    argv = cmd[sep + 1 :]
    assert len(argv) == 3
    display_line = cmd[4]
    assert "sound name (item 3 of argv)" in display_line


def test_build_command_truncates_long_inputs() -> None:
    backend = NotificationBackend(force_enabled=True)
    body = "x" * 500
    title = "y" * 200
    cmd = backend.build_command(body=body, title=title, sound_name="")
    sep = cmd.index("--")
    argv = cmd[sep + 1 :]
    assert len(argv[0]) == 240
    assert len(argv[1]) == 80


# ---------------------------------------------------------------------------
# Injection regression — payload becomes a literal argv item, no shell exec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        '"; do shell script "echo PWNED"; display notification "',
        "`echo PWNED`",
        "$(echo PWNED)",
        "'; tell application \"Finder\" to delete every file; '",
    ],
)
async def test_injection_payload_is_literal_argv_no_shell_exec(evil: str) -> None:
    backend = NotificationBackend(force_enabled=True)
    captured_argv: list[list[str]] = []

    async def fake_exec(*args, stdout=None, stderr=None):
        captured_argv.append(list(args))
        proc = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        proc.stderr = None
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await backend.fire(
            IndicationKind.ACTION_FAILED,
            IndicationLevel.ERROR,
            "title",
            evil,
            {},
        )

    assert len(captured_argv) == 1
    cmd = captured_argv[0]
    sep = cmd.index("--")
    argv = cmd[sep + 1 :]
    # The evil payload is delivered as the FIRST argv item, byte-for-byte.
    assert argv[0] == evil[:240], (
        f"payload should be a literal argv item, got: {argv[0]!r}"
    )
    # The script source has only fixed `-e` strings — no payload interpolation.
    script_strings = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-e"]
    for s in script_strings:
        assert evil not in s, "payload must NOT appear in script source"
        assert "PWNED" not in s
        assert "Finder" not in s


# ---------------------------------------------------------------------------
# Lifecycle: timeout, non-zero rc, non-Darwin disable
# ---------------------------------------------------------------------------


async def test_subprocess_timeout_kills_proc() -> None:
    backend = NotificationBackend(force_enabled=True)
    proc = MagicMock()

    async def hang(*a, **kw):
        await asyncio.sleep(10)

    proc.wait = AsyncMock(side_effect=hang)
    proc.kill = MagicMock()
    proc.stderr = None
    proc.returncode = None

    async def fake_exec(*args, stdout=None, stderr=None):
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        # Force a tighter timeout for fast test by patching asyncio.wait_for
        # to wrap with a 0.05s ceiling? Easier: just trust the hang and set
        # a tighter wait. We patch wait_for to use 0.05s.
        with patch("asyncio.wait_for", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            await backend.fire(
                IndicationKind.ACTION_FAILED,
                IndicationLevel.ERROR,
                "t",
                "b",
                {},
            )
    proc.kill.assert_called_once()


async def test_non_darwin_disables_backend(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    backend = NotificationBackend()
    assert backend.enabled is False
    # fire() is a no-op — no subprocess call.
    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await backend.fire(
            IndicationKind.ACTION_FAILED,
            IndicationLevel.ERROR,
            "t",
            "b",
            {},
        )
    exec_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Level → sound name mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level,expected_sound",
    [
        (IndicationLevel.ATTENTION, "Sosumi"),
        (IndicationLevel.ERROR, "Sosumi"),
        (IndicationLevel.INPUT_WAITING, "Glass"),
        (IndicationLevel.SUCCESS, ""),
        (IndicationLevel.INFO, ""),
        (IndicationLevel.LONG_RUNNING, ""),
    ],
)
async def test_level_to_sound_mapping(level: IndicationLevel, expected_sound: str) -> None:
    backend = NotificationBackend(force_enabled=True)
    captured_argv: list[list[str]] = []

    async def fake_exec(*args, stdout=None, stderr=None):
        captured_argv.append(list(args))
        proc = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        proc.returncode = 0
        proc.stderr = None
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await backend.fire(
            IndicationKind.ACTION_FAILED, level, "t", "b", {}
        )
    cmd = captured_argv[0]
    sep = cmd.index("--")
    argv = cmd[sep + 1 :]
    if expected_sound:
        assert argv[2] == expected_sound
    else:
        assert len(argv) == 2  # no sound_name passed


async def test_aclose_is_noop() -> None:
    backend = NotificationBackend(force_enabled=True)
    await backend.aclose()
