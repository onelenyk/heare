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
    from .conversation import ConversationManager
    from .storage import TranscriptStore


# Keys from build() that intentionally do NOT flow into the generator prompt.
# This is asserted in tests/test_context.py to prevent silent drift when
# build() grows new fields.
_EXCLUDED_FROM_GENERATOR_CTX: frozenset[str] = frozenset({
    "mode",
    "heartbeat_flag",
    "transcript_or_heartbeat",
    "speaker_rule_block",
    "silence_block",
    "proactivity_block",
    "conversation_active",
    "conversation_summary",
    "active_topics",
    "entities",
    "recent_turns",
})


class ContextBuilder:
    def __init__(
        self,
        store: "TranscriptStore",
        settings: "Settings",
        conversation_manager: "ConversationManager | None" = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.conversation_manager = conversation_manager

    async def build(
        self,
        transcript: str | None,
        heartbeat: bool = False,
        keep_placeholders: list[str] | None = None,
        speaker_id: str | None = None,
        conversation_id: int | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now().astimezone()
        recent = await self.store.recent_transcripts(n=5)
        keep = set(keep_placeholders or ())

        conversation_ctx: dict[str, Any]
        if self.conversation_manager is not None and conversation_id is not None:
            raw = await self.conversation_manager.build_context(conversation_id)
            conversation_ctx = {
                "conversation_active": "yes" if raw.get("conversation_active") else "no",
                "conversation_summary": raw.get("conversation_summary", ""),
                "active_topics": raw.get("active_topics", []),
                "entities": raw.get("entities", {}),
                "recent_turns": raw.get("recent_turns", []),
            }
        else:
            conversation_ctx = {
                "conversation_active": "no",
                "conversation_summary": "",
                "active_topics": [],
                "entities": {},
                "recent_turns": [],
            }

        ctx: dict[str, Any] = {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": str(now.tzinfo),
            "mode": self.settings.mode.value,
            "heartbeat_flag": "yes" if heartbeat else "no",
            "recent_transcripts": self._format_recent(recent),
            "transcript_or_heartbeat": self._format_input(transcript, heartbeat),
            "speaker_rule_block": self._render_rule_block(speaker_id=speaker_id),
            "silence_block": self._render_silence_block(recent, now.timestamp()),
            "proactivity_block": self._render_proactivity_block(),
            "conversation_active": conversation_ctx["conversation_active"],
            "conversation_summary": conversation_ctx["conversation_summary"],
            "active_topics": ", ".join(conversation_ctx["active_topics"]),
            "entities": self._format_entities(conversation_ctx["entities"]),
            "recent_turns": self._format_recent_turns(conversation_ctx["recent_turns"]),
        }
        # Speculative-path support: if a caller asks for a placeholder to
        # remain literal (so it can be substituted later with a real value),
        # overwrite the rendered value with the literal `{name}` form so
        # _safe_substitute leaves it untouched during render.
        for key in keep:
            ctx[key] = "{" + key + "}"
        return ctx

    async def build_for_generator(
        self,
        transcript: str,
        persona: str,
    ) -> dict[str, Any]:
        """Minimal context for the Phase-1 generator prompt.

        Shares the time/timezone/recent-transcripts computation with build()
        so any new field added there is visible here (or flagged by the
        _EXCLUDED_FROM_GENERATOR_CTX drift test).
        """
        full = await self.build(transcript=None, heartbeat=False)
        result = {k: v for k, v in full.items() if k not in _EXCLUDED_FROM_GENERATOR_CTX}
        result["persona"] = persona
        result["transcript"] = transcript
        return result

    def _render_silence_block(self, recent: list[dict], now_ts: float) -> str:
        """Return a one-line context note about how long the room has been quiet."""
        if not recent:
            return ""
        last_ts = recent[-1]["ts"]
        silence_s = max(0, int(now_ts - last_ts))
        active = silence_s < 300
        return (
            f"Silence since last utterance: {silence_s}s. "
            f"Conversation active: {'yes' if active else 'no'}."
        )

    def _render_proactivity_block(self) -> str:
        """Return a prompt override based on proactivity_level setting."""
        level = self.settings.proactivity_level
        if level == "low":
            return "PROACTIVITY OVERRIDE: reserved — stay quiet unless clearly helpful.\n"
        if level == "high":
            return "PROACTIVITY OVERRIDE: high — be very engaged, initiate topics, ask follow-ups freely.\n"
        return ""  # medium: prompt defaults apply, no override needed

    def _render_rule_block(self, speaker_id: str | None = None) -> str:
        if not self.settings.speaker_id_enabled:
            return ""
        if speaker_id == "owner":
            return "Speaker: owner (high confidence)"
        if speaker_id is None:
            return "Speaker: likely owner (utterance below ID threshold — treat as owner)"
        return f"Speaker: {speaker_id} (not owner)"

    def _format_recent(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "(none)"
        redact = self.settings.speaker_id_enabled
        lines = []
        for row in rows:
            stamp = dt.datetime.fromtimestamp(row["ts"]).strftime("%H:%M:%S")
            if redact and row.get("speaker_id") != "owner":
                text = "[REDACTED]"
            else:
                text = row["text"]
            lines.append(f"  - [{stamp}] {text}")
        return "\n".join(lines)

    def _format_input(self, transcript: str | None, heartbeat: bool) -> str:
        if heartbeat:
            return "(heartbeat tick — no new transcript)"
        if transcript is None:
            return "(empty)"
        return transcript

    def _format_entities(self, entities: dict[str, Any]) -> str:
        """Format entities dictionary for display in prompt."""
        if not entities:
            return ""
        entity_lines = []
        for category, items in entities.items():
            if isinstance(items, list):
                entity_lines.append(f"{category}: {', '.join(str(item) for item in items)}")
            else:
                entity_lines.append(f"{category}: {str(items)}")
        return "\n".join(f"  - {line}" for line in entity_lines)

    def _format_recent_turns(self, recent_turns: list[dict[str, Any]]) -> str:
        """Format recent turns with topics for display in prompt."""
        if not recent_turns:
            return "(none)"
        lines = []
        for turn in recent_turns:
            timestamp = dt.datetime.fromtimestamp(turn["start_ts"]).strftime("%H:%M:%S")
            text = turn["aggregated_text"]
            topics = turn.get("topic_tags", [])
            topic_line = f" (topics: {', '.join(topics)})" if topics else ""
            lines.append(f"  - [{timestamp}] {text}{topic_line}")
        return "\n".join(lines)

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
