"""Context dict + safe template substitution for the decider prompt.

Uses regex-based placeholder substitution rather than str.format() so the
JSON example literal in prompts/decider.txt doesn't get parsed as format
specifiers.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Settings
    from .storage import TranscriptStore


class ContextBuilder:
    def __init__(self, store: "TranscriptStore", settings: "Settings") -> None:
        self.store = store
        self.settings = settings

    async def build(
        self, transcript: str | None, heartbeat: bool = False
    ) -> dict[str, Any]:
        now = dt.datetime.now().astimezone()
        recent = await self.store.recent_transcripts(n=5)
        return {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": str(now.tzinfo),
            "mode": self.settings.mode.value,
            "heartbeat_flag": "yes" if heartbeat else "no",
            "recent_transcripts": self._format_recent(recent),
            "transcript_or_heartbeat": self._format_input(transcript, heartbeat),
        }

    def _format_recent(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "(none)"
        lines = []
        for row in rows:
            stamp = dt.datetime.fromtimestamp(row["ts"]).strftime("%H:%M:%S")
            lines.append(f"  - [{stamp}] {row['text']}")
        return "\n".join(lines)

    def _format_input(self, transcript: str | None, heartbeat: bool) -> str:
        if heartbeat:
            return "(heartbeat tick — no new transcript)"
        if transcript is None:
            return "(empty)"
        return transcript

    def render(self, template: str, ctx: dict[str, Any]) -> str:
        return _safe_substitute(template, ctx)


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _safe_substitute(template: str, ctx: dict[str, Any]) -> str:
    def repl(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in ctx:
            return str(ctx[key])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(repl, template)
