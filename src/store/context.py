"""Context dict + safe template substitution for prompt rendering.

Uses regex-based placeholder substitution rather than str.format() to avoid
parse errors on JSON-like content in prompt templates.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import Settings
    from src.store.conversation import ConversationManager
    from src.store.storage import TranscriptStore

from src.voice.language.core import LANG_NAMES


# Keys from build() that intentionally do NOT flow into the generator prompt.
# This is asserted in tests/test_context.py to prevent silent drift when
# build() grows new fields.
_EXCLUDED_FROM_GENERATOR_CTX: frozenset[str] = frozenset({
    "mode",
    "heartbeat_flag",
    "transcript_or_heartbeat",
    "silence_block",
    "proactivity_block",
    "conversation_active",  # internal yes/no flag, not surfaced to generator
    # Phase 2.2: the following 4 keys now flow into build_for_generator:
    # conversation_summary, active_topics, entities, recent_turns
})


class ContextBuilder:
    def __init__(
        self,
        store: "TranscriptStore",
        settings: "Settings",
        conversation_manager: "ConversationManager | None" = None,
        project_dir: str | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.conversation_manager = conversation_manager
        self._project_dir = project_dir
        self._mcp_bridge: Any = None
        self._session_state: Any = None
        self._mcp_descriptions: str | None = None
        try:
            from src.skills.mcp_utils import build_mcp_prompt_block, read_mcp_servers

            servers = read_mcp_servers(settings.workspace_dir)
            block = build_mcp_prompt_block(servers)
            if block:
                self._mcp_descriptions = block
        except Exception:  # noqa: BLE001 — MCP discovery is best-effort
            self._mcp_descriptions = None

    def set_session_state(self, session_state: Any) -> None:
        """Attach the live SessionState so the system prompt carries the
        active mode's behavior addendum + the available-mode list."""
        self._session_state = session_state

    def set_mcp_bridge(self, bridge: Any) -> None:
        """Attach the live MCP bridge so the system prompt advertises the
        actually-connected tools instead of the static .mcp.json names.

        Called from build_pipeline once connect_mcp_servers() has run.
        """
        self._mcp_bridge = bridge

    async def build(
        self,
        transcript: str | None,
        heartbeat: bool = False,
        keep_placeholders: list[str] | None = None,
        conversation_id: int | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now().astimezone()
        recent = await self.store.recent_transcripts(
            n=self.settings.context_recent_transcripts_count
        )
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
        conversation_id: int | None = None,
        user_language: str = "en",
    ) -> dict[str, Any]:
        """Context for the generator prompt (Phase 2.2: with conversation memory).

        Shares time/timezone/recent-transcripts/conversation-* fields with
        build() via projection. Adds persona, transcript, and the
        bfg-only recent_actions key.
        """
        full = await self.build(
            transcript=None,
            heartbeat=False,
            conversation_id=conversation_id,
        )
        result = {k: v for k, v in full.items() if k not in _EXCLUDED_FROM_GENERATOR_CTX}
        result["persona"] = persona
        result["transcript"] = transcript
        result["user_language"] = LANG_NAMES.get(user_language, "English")
        # recent_actions — pulled directly from ConversationManager's
        # in-memory log; not persisted in build() output.
        if self.conversation_manager is not None:
            result["recent_actions"] = self._format_recent_actions(
                self.conversation_manager.recent_actions()
            )
        else:
            result["recent_actions"] = "(none)"
        if self._project_dir:
            result["project_dir"] = self._project_dir
        result["workspace_dir"] = str(self.settings.workspace_dir)
        result["canvas_info"] = (
            "Canvas widget (use show_text / show_canvas): "
            "full-width iframe, ~500px tall. Use window.innerWidth/Height "
            "to dynamically size your content. The iframe has 2px border — "
            "usable area is slightly smaller than nominal size. "
            "Use body { margin:0; overflow:hidden }. "
            "Fonts: system-ui, monospace. Inline CSS only."
        )
        live_mcp = None
        if self._mcp_bridge is not None:
            try:
                live_mcp = self._mcp_bridge.prompt_block()
            except Exception:  # noqa: BLE001 — never break the turn
                live_mcp = None
        if live_mcp:
            result["mcp_servers"] = live_mcp
        elif self._mcp_descriptions:
            result["mcp_servers"] = self._mcp_descriptions
        if self._session_state is not None:
            try:
                profile = self._session_state.profile
                from src.agent.modes import VALID_MODES

                # Build mode-gate language from the profile data.
                # The mode is an output gate — it constrains the channel,
                # not the persona. The LLM is still itself; only the output
                # rules change.
                _output_rules = {
                    "ambient": (
                        "Voice output ON. Full engagement. Follow your "
                        "natural curiosity and speak freely."
                    ),
                    "focus": (
                        "Voice output ON but MINIMAL. Speak only when "
                        "directly addressed or asked a clear question. "
                        "Be terse and fast — answer and stop."
                    ),
                    "silent": (
                        "Voice output OFF — your speech is muted. You can "
                        "still think and use side-effect-free tools, but "
                        "emit nothing via voice. Use text/canvas if needed."
                    ),
                    "assistant": (
                        "Voice output ON. Proactive helper — full tool "
                        "access, may offer follow-ups and do multi-step work."
                    ),
                    "meeting": (
                        "Voice output OFF — your speech is muted. "
                        "Passive note-taker only. Do not converse or act; "
                        "side-effect tools are blocked."
                    ),
                }
                output_rule = _output_rules.get(
                    profile.name,
                    f"Voice output ON. Standard engagement."
                )

                result["mode_block"] = (
                    f"MODE GATE: {profile.name}\n"
                    f"{output_rule}\n"
                    "This is a channel constraint, not a personality change. "
                    "You are still yourself — the gate only limits your "
                    "output channel, not who you are.\n"
                    f"Available modes (switch with set_mode): "
                    f"{', '.join(VALID_MODES)}.\n"
                    "If the user tells you to be quiet / stop talking "
                    "(e.g. 'помовчи', 'тихо', 'be quiet', 'stop talking', "
                    "'hush'), offer to switch to silent mode — ask a brief "
                    "one-line confirmation, and on agreement call "
                    "set_mode('silent')."
                )

            except Exception:  # noqa: BLE001 — never break the turn
                pass
        try:
            disp = await self.store.latest_display()
        except Exception:  # noqa: BLE001 — never break the turn
            disp = None
        if disp and disp.get("content"):
            content = str(disp["content"])
            preview = content if len(content) <= 600 else content[:600] + " …"
            label = disp.get("title") or disp.get("format") or "display"
            result["current_display"] = (
                f"Currently shown on the screen panel "
                f"({label}, format={disp.get('format')}):\n{preview}\n"
                "This is what the user sees right now — you put it there with "
                "show_display. Refer to it naturally ('as shown on screen'); "
                "call show_display again to replace it with updated content."
            )
        # Inject active sub-agent status
        try:
            from src.agent.subagent_manager import get_agent_manager
            mgr = get_agent_manager()
            if mgr is not None:
                active = mgr.list_active()
                if active:
                    lines = ["Active sub-agents:"]
                    for a in active:
                        sid = a.get("session_id", "")[:12]
                        prompt = a.get("prompt", "")[:60]
                        status = a.get("status", "?")
                        icon = {"running": "⚙", "starting": "⟳", "waiting_for_input": "⚠", "done": "✓", "error": "✗", "cancelled": "✗"}.get(status, "?")
                        base = f"  - {sid}: {prompt}"
                        if status == "waiting_for_input":
                            lines.append(f"{base} — waiting for input {icon} ({a.get('age_seconds', 0)}s ago)")
                        elif status in ("running", "starting"):
                            tc = a.get("tool_calls", 0)
                            c = a.get("cost") or 0
                            extra = f" ({tc} tools, ${c:.4f})" if tc else ""
                            lines.append(f"{base} — {status} {icon}{extra}")
                        else:
                            c = a.get("cost") or 0
                            lines.append(f"{base} — {status} {icon} (${c:.4f})")
                    result["sub_agents_block"] = "\n".join(lines)
        except Exception:
            pass  # never break context building
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

    def _format_recent(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "(none)"
        lines = []
        for row in rows:
            stamp = dt.datetime.fromtimestamp(row["ts"]).strftime("%H:%M:%S")
            line = f"  - [{stamp}] {row['text']}"
            lines.append(line)
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

    def _format_recent_actions(self, actions: list[dict[str, Any]]) -> str:
        """Format recent action-log entries for the generator prompt.

        Example output:
            - [14:23] (5m ago) ✓ bash: додав хліб
            - [14:25] (3m ago) ⋯ search: пошук рейсів
            - [14:27] (1m ago) ✗ bash: помилка — ...

        CCS-02: items-first rendering for web_search/web_fetch entries.
        When ``entry["items"]`` is present and non-empty AND the tool is
        ``web_search``/``web_fetch``, build a numbered list from items
        (NOT in addition to the legacy ``result`` blob). Hard cap each web
        entry at ``WEB_TAIL_LIMIT`` chars (1800) using TAIL-FIRST
        truncation: drop trailing items first and append a
        ``"(N more items truncated)"`` suffix.

        CCS-04: each entry is annotated with both an absolute ``[HH:MM:SS]``
        timestamp AND a relative ``(Nm ago)`` / ``(Ns ago)`` / ``(Nh ago)``
        annotation so the generator prompt can apply the
        ``refinement_recency_seconds`` (default 600s = 10 min) recency
        window for the recency filter. The
        relative form keeps the rule expressible in plain language to the
        LLM without requiring it to reason about wallclock arithmetic.

        Fallback: when ``items`` is absent (or empty), the legacy
        ``result`` rendering path is used unchanged — this keeps existing
        web_search entries (and tests like
        ``test_format_recent_actions_keeps_web_search_content`` /
        ``test_format_recent_actions_truncates_other_tools``) working
        exactly as before. Non-web tools always use the 80-char tail cap.
        """
        if not actions:
            return "(none)"
        glyph = {"pending": "⋯", "done": "✓", "error": "✗"}
        # Both the constant below and the AC use the SAME 1800 — no drift.
        WEB_TAIL_LIMIT = 1800
        OTHER_TAIL_LIMIT = 80
        now_ts = dt.datetime.now().timestamp()
        lines: list[str] = []
        for a in actions:
            entry_ts = a.get("ts", 0) or 0
            ts = dt.datetime.fromtimestamp(entry_ts).strftime("%H:%M:%S")
            rel = self._format_relative_age(now_ts - entry_ts)
            status = a.get("status", "pending")
            tool = a.get("tool", "")
            args = a.get("args", "")[:60]
            items = a.get("items")
            if (
                tool in ("web_search", "web_fetch")
                and isinstance(items, list)
                and items
            ):
                rendered = self._render_items_with_tail_truncation(
                    items, WEB_TAIL_LIMIT
                )
                lines.append(
                    f"  - [{ts}] {rel} {glyph.get(status, '?')} {tool}: {rendered}"
                )
                continue
            # Legacy fallback path: render from `result` blob. Existing tests
            # at tests/test_context.py:391-440 exercise this path for entries
            # without `items` — keep behavior unchanged.
            tail = a.get("result") or a.get("error") or args
            tail_limit = (
                WEB_TAIL_LIMIT
                if tool in ("web_search", "web_fetch")
                else OTHER_TAIL_LIMIT
            )
            lines.append(
                f"  - [{ts}] {rel} {glyph.get(status, '?')} {tool}: {tail[:tail_limit]}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_relative_age(age_seconds: float) -> str:
        """Render an age (seconds) as a compact ``(Ns ago)`` / ``(Nm ago)``
        / ``(Nh ago)`` annotation for the generator prompt's REFINEMENT
        recency window. Negative or zero ages render as ``(just now)``.
        """
        if age_seconds < 1:
            return "(just now)"
        if age_seconds < 60:
            return f"({int(age_seconds)}s ago)"
        if age_seconds < 3600:
            return f"({int(age_seconds // 60)}m ago)"
        return f"({int(age_seconds // 3600)}h ago)"

    @staticmethod
    def _render_items_with_tail_truncation(
        items: list[dict[str, Any]], char_cap: int
    ) -> str:
        """Render numbered items with tail-first truncation.

        Builds ``"{n}. {title}\\n{snippet}\\n{url}"`` blocks separated by
        blank lines. When the joined string exceeds ``char_cap``, drop
        items from the tail (one at a time) and append a
        ``"(N more items truncated)"`` suffix. The final string is
        guaranteed to be ``<= char_cap`` chars.
        """
        def _render_block(it: dict[str, Any]) -> str:
            n = it.get("n", 0)
            title = (it.get("title") or "").strip()
            snippet = (it.get("snippet") or "").strip()
            url = (it.get("url") or "").strip()
            parts = [f"{n}. {title}"] if title else [f"{n}."]
            if snippet:
                parts.append(snippet)
            if url:
                parts.append(url)
            return "\n".join(parts)

        rendered_blocks = [_render_block(it) for it in items]
        joined = "\n\n".join(rendered_blocks)
        if len(joined) <= char_cap:
            return joined

        # Tail-first truncation: drop blocks from the end until the
        # joined output PLUS the suffix fits the cap.
        kept = list(rendered_blocks)
        while kept:
            dropped = len(rendered_blocks) - len(kept)
            if dropped > 0:
                suffix = f"\n({dropped} more items truncated)"
            else:
                suffix = ""
            candidate = "\n\n".join(kept) + suffix
            if len(candidate) <= char_cap and dropped > 0:
                return candidate
            if len(candidate) <= char_cap:
                # No drop yet but somehow fits — should not happen given
                # the early-return above; keep for safety.
                return candidate
            kept.pop()

        # Pathological: even a single block is too long — hard truncate
        # the first block and tag the rest as truncated.
        dropped = len(rendered_blocks)
        suffix = f"\n({dropped} more items truncated)"
        budget = max(0, char_cap - len(suffix))
        return rendered_blocks[0][:budget] + suffix

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
