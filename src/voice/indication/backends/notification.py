"""macOS Notification Center backend.

Body and title are passed as subprocess argv items — NEVER interpolated into
the AppleScript source. AppleScript reads them via `item N of argv`. This
makes the backend immune to AppleScript / shell injection from arbitrary
tool error text or remote content.

On non-Darwin platforms the backend disables itself at construction; fire()
becomes a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.voice.indication.core import IndicationKind, IndicationLevel

logger = logging.getLogger("heare.indication.notification")


_LEVEL_TO_SOUND_NAME: dict[str, str] = {
    "attention": "Sosumi",
    "error": "Sosumi",
    "input_waiting": "Glass",
    # info / success / long_running → silent banner (no sound name passed)
}


def _level_to_sound(level: str) -> str:
    return _LEVEL_TO_SOUND_NAME.get(level, "")


class NotificationBackend:
    name = "notification"

    def __init__(self, *, force_enabled: bool | None = None) -> None:
        # `force_enabled` is for tests on non-Darwin CI — defaults to platform
        # detection. Production code never passes it.
        if force_enabled is None:
            self._enabled = sys.platform == "darwin"
        else:
            self._enabled = bool(force_enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def build_command(self, body: str, title: str, sound_name: str) -> list[str]:
        """Construct the osascript argv. Pure helper — no side effects.

        Body, title, and sound_name pass via subprocess argv items after `--`.
        AppleScript reads them via `item N of argv`. The `sound name` clause
        is omitted entirely when sound_name is empty (argv length differs).
        """
        body = (body or "")[:240]
        title = (title or "heare")[:80]
        if sound_name:
            display_line = (
                "display notification (item 1 of argv) "
                "with title (item 2 of argv) "
                "sound name (item 3 of argv)"
            )
            argv_extras = [body, title, sound_name]
        else:
            display_line = (
                "display notification (item 1 of argv) "
                "with title (item 2 of argv)"
            )
            argv_extras = [body, title]
        cmd = [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            display_line,
            "-e",
            "end run",
            "--",
            *argv_extras,
        ]
        return cmd

    async def fire(
        self,
        kind: "IndicationKind",
        level: "IndicationLevel",
        title: str,
        body: str,
        meta: dict,
    ) -> None:
        if not self._enabled:
            return
        sound_name = _level_to_sound(level.value)
        cmd = self.build_command(body, title, sound_name)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("indication.notification: spawn failed", exc_info=True)
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("indication.notification: osascript timed out, killed")
            return
        if proc.returncode != 0:
            stderr_b = b""
            if proc.stderr is not None:
                try:
                    stderr_b = await proc.stderr.read()
                except Exception:  # noqa: BLE001
                    pass
            logger.warning(
                "indication.notification: osascript rc=%s stderr=%r",
                proc.returncode,
                stderr_b[:200],
            )

    async def aclose(self) -> None:
        return
