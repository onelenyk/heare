"""ConversationManager maintains conversation state: topics, entities, summary."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .claude_backend_common import ClaudeBackend
    from .openrouter_topic_extractor import OpenRouterTopicExtractorCLI
    from .storage import TranscriptStore


logger = logging.getLogger("heare.conversation")


class ConversationManager:
    """Maintains conversation state: topics, entities, summary, recent actions.

    Phase 2.2: adds an in-memory `_action_log` (bounded deque) updated by
    sync `record_action_*` methods from the generator hot path and the
    action worker callbacks. Single event loop → dict/deque mutations are
    atomic under the GIL; no lock required.
    """

    _ACTION_LOG_MAXLEN = 16

    def __init__(
        self,
        store: TranscriptStore,
        claude_cli: ClaudeBackend,
        *,
        topic_extractor: "OpenRouterTopicExtractorCLI | None" = None,
    ) -> None:
        """Initialize ConversationManager.

        Args:
            store: TranscriptStore for database operations
            claude_cli: ClaudeBackend for topic extraction (Claude path; default).
            topic_extractor: Optional OpenRouter-backed topic extractor. When
                provided, ``extract_topics`` routes here instead of calling
                ``claude_cli.call_decider``. Default: None (use Claude path).
        """
        import collections

        self.store = store
        self.claude = claude_cli
        self._topic_extractor = topic_extractor
        self._action_log: collections.deque = collections.deque(
            maxlen=self._ACTION_LOG_MAXLEN
        )

    # ---- Phase 2.2 action log (sync, no lock — single event loop) ----
    # CCS-01: each record_* mutation also fires a fire-and-forget SQLite
    # UPSERT (write-through projection). DB failures log a warning but do
    # NOT raise; the in-memory deque is the source of truth within a turn.

    def record_action_pending(self, intent_id: int, tool: str, args: str) -> None:
        ts = time.time()
        self._action_log.append({
            "id": intent_id,
            "tool": tool,
            "args": args,
            "status": "pending",
            "ts": ts,
        })
        self._schedule_persist(
            intent_id=intent_id,
            tool=tool,
            args=args,
            status="pending",
            result=None,
            ts=ts,
        )

    def record_action_result(
        self,
        intent_id: int,
        summary: str,
        *,
        items: list[dict] | None = None,
    ) -> None:
        """Record a completed action.

        CCS-02: when ``items`` is provided (e.g. structured web_search
        hits), it is stored on the in-memory entry as ``entry["items"]``
        and persisted into ``result_json`` as
        ``{"summary": summary, "items": items}``. Backward compatible:
        callers that pass only ``summary`` produce ``{"summary": summary}``.
        """
        # Update the matching pending entry if present; otherwise append a
        # fresh "done" entry. Both branches are O(n) over maxlen=16 = trivial.
        ts = time.time()
        if items is not None:
            persisted = json.dumps({"summary": summary, "items": items})
        else:
            persisted = json.dumps({"summary": summary})
        for entry in self._action_log:
            if entry["id"] == intent_id and entry["status"] == "pending":
                entry["status"] = "done"
                entry["result"] = summary
                entry["ts"] = ts
                if items is not None:
                    entry["items"] = items
                self._schedule_persist(
                    intent_id=intent_id,
                    tool=entry.get("tool", ""),
                    args=entry.get("args", ""),
                    status="done",
                    result=persisted,
                    ts=ts,
                )
                return
        new_entry: dict[str, Any] = {
            "id": intent_id,
            "tool": "",
            "args": "",
            "status": "done",
            "result": summary,
            "ts": ts,
        }
        if items is not None:
            new_entry["items"] = items
        self._action_log.append(new_entry)
        self._schedule_persist(
            intent_id=intent_id,
            tool="",
            args="",
            status="done",
            result=persisted,
            ts=ts,
        )

    def record_action_error(self, intent_id: int, error: str) -> None:
        ts = time.time()
        for entry in self._action_log:
            if entry["id"] == intent_id and entry["status"] == "pending":
                entry["status"] = "error"
                entry["error"] = error
                entry["ts"] = ts
                self._schedule_persist(
                    intent_id=intent_id,
                    tool=entry.get("tool", ""),
                    args=entry.get("args", ""),
                    status="error",
                    result=json.dumps({"error": error}),
                    ts=ts,
                )
                return
        self._action_log.append({
            "id": intent_id,
            "tool": "",
            "args": "",
            "status": "error",
            "error": error,
            "ts": ts,
        })
        self._schedule_persist(
            intent_id=intent_id,
            tool="",
            args="",
            status="error",
            result=json.dumps({"error": error}),
            ts=ts,
        )

    def _schedule_persist(
        self,
        *,
        intent_id: int,
        tool: str,
        args: str,
        status: str,
        result: str | None,
        ts: float,
    ) -> None:
        """Fire-and-forget the SQLite UPSERT.

        CCS-01: write-through to the action_log projection. Errors (DB
        locked, store closed, no running loop) are logged and swallowed —
        the in-memory deque update has already succeeded.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (sync test path) — skip persistence silently.
            return
        coro = self._persist_action_entry_safe(
            intent_id=intent_id,
            tool=tool,
            args=args,
            status=status,
            result=result,
            ts=ts,
        )
        loop.create_task(coro)

    async def _persist_action_entry_safe(
        self,
        *,
        intent_id: int,
        tool: str,
        args: str,
        status: str,
        result: str | None,
        ts: float,
    ) -> None:
        try:
            await self.store.upsert_action_log_entry(
                intent_id=intent_id,
                tool=tool,
                args=args,
                status=status,
                result=result,
                ts=ts,
            )
        except Exception as e:  # noqa: BLE001 — DB lock / store closed are non-fatal
            logger.warning(
                "action_log persist failed intent_id=%d status=%s: %s",
                intent_id,
                status,
                e,
            )

    async def hydrate_action_log(
        self, *, since_ts: float | None = None
    ) -> None:
        """Rebuild the in-memory action log from SQLite on startup.

        CCS-01: loads up to ``_ACTION_LOG_MAXLEN`` rows from the actions
        table (intent_id IS NOT NULL only — legacy decision-only rows are
        skipped). When ``since_ts`` is provided, rows older than that are
        also skipped to prevent stale-context pollution across the
        conversation idle boundary (default 30 min).
        """
        try:
            rows = await self.store.load_recent_action_log(
                limit=self._ACTION_LOG_MAXLEN,
                since_ts=since_ts,
            )
        except Exception as e:  # noqa: BLE001 — non-fatal; deque stays empty
            logger.warning("action_log hydrate failed: %s", e)
            return

        # rows are newest-first; append in chronological order so the
        # deque preserves natural append order (oldest at left, newest right).
        self._action_log.clear()
        for row in reversed(rows):
            entry: dict[str, Any] = {
                "id": row["intent_id"],
                "tool": row["tool"] or "",
                "args": row["args"] or "",
                "status": row["status"],
                "ts": row["ts"],
            }
            raw = row.get("result_json")
            if raw:
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    if "summary" in parsed:
                        entry["result"] = parsed["summary"]
                    if "error" in parsed:
                        entry["error"] = parsed["error"]
                    # CCS-02: structured search results survive restart.
                    if isinstance(parsed.get("items"), list):
                        entry["items"] = parsed["items"]
            self._action_log.append(entry)

    def recent_actions(self, limit: int = 5) -> list[dict]:
        """Return a snapshot of the last `limit` actions, newest-first."""
        snapshot = list(self._action_log)
        return list(reversed(snapshot[-limit:])) if snapshot else []

    async def extract_topics(self, text: str) -> list[str]:
        """Extract topic tags from aggregated turn text.

        Uses lightweight Claude call with structured output:
        - Return top 3-5 topics as short phrases (2-4 words each)
        - Examples: "weather forecast", "calendar meeting", "code debugging"

        Args:
            text: Aggregated turn text to extract topics from

        Returns:
            List of topic phrases (2-4 words each)
        """
        prompt = f"""Extract 3-5 topic phrases from the following text.
Return ONLY a JSON array of short phrases (2-4 words each).
No explanation, no markdown, just the JSON array.

Text: {text}

Example format: ["weather forecast", "calendar meeting", "code debugging"]"""

        t_start = time.monotonic()
        backend_name = "openrouter" if self._topic_extractor is not None else "claude"
        topics: list[str] = []
        try:
            if self._topic_extractor is not None:
                topics = await self._topic_extractor.extract_topics(prompt)
                return topics

            # Claude path
            try:
                response = await self.claude.call_decider(prompt)
                # Parse response - might be JSON or text
                if isinstance(response, dict):
                    # If it's a dict, look for expected keys
                    if "result" in response:
                        response_text = response["result"]
                    else:
                        # Fallback: try to extract from reply field
                        response_text = response.get("reply", str(response))
                else:
                    response_text = str(response)

                # Clean up markdown fences if present
                response_text = response_text.strip()
                if response_text.startswith("```"):
                    # Remove markdown fence
                    lines = response_text.split("\n")
                    response_text = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[0]

                # Parse JSON
                parsed_topics = json.loads(response_text)
                if isinstance(parsed_topics, list):
                    topics = [str(t) for t in parsed_topics[:5]]  # Max 5 topics
                return topics

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to extract topics from text: %s", e)
                topics = []
                return topics
        finally:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            logger.info(
                "[TOPIC EXTRACT backend=%s ms=%d topics=%d]",
                backend_name,
                elapsed_ms,
                len(topics),
            )

    async def update_summary(
        self,
        conversation_id: int,
        turn_text: str,
        topics: list[str],
    ) -> None:
        """Update conversation summary with new turn.

        Strategy:
        - Keep last 3 turns verbatim
        - Older turns → summarize into key points
        - Track entities: people, places, dates, projects
        - Maintain "active topics" set (mentioned in last 2 turns)

        Args:
            conversation_id: Database ID of conversation
            turn_text: Aggregated text of the new turn
            topics: Extracted topics for this turn
        """
        # Get recent turns to build context
        recent_turns = await self.store.get_recent_turns(conversation_id, n=5)

        # Build summary
        if len(recent_turns) <= 3:
            # Keep everything verbatim if we have 3 or fewer turns
            summary_parts = [turn["aggregated_text"] for turn in recent_turns]
            summary_parts.append(turn_text)
            summary = " | ".join(summary_parts)
        else:
            # Keep last 3 verbatim, summarize older ones
            older_turns = recent_turns[:-3]
            recent_verbatim = recent_turns[-3:]

            older_summary = " | ".join(
                [f"[{i+1}] {turn['aggregated_text'][:50]}..." for i, turn in enumerate(older_turns)]
            )
            verbatim_summary = " | ".join([turn["aggregated_text"] for turn in recent_verbatim])

            summary = f"Earlier: {older_summary} | Recent: {verbatim_summary} | Latest: {turn_text}"

        # Extract entities from turn text and topics
        # Simple entity extraction: look for capitalized words, dates, etc.
        entity_map: dict[str, Any] = {}
        if topics:
            entity_map["topics"] = topics

        # Look for capitalized phrases (potential entities)
        import re

        capitalized = re.findall(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b", turn_text)
        if capitalized:
            entity_map["mentioned"] = capitalized[:5]  # Limit to 5

        # Update conversation in database
        await self.store.update_conversation_summary(
            conversation_id=conversation_id,
            summary=summary,
            entity_map=entity_map,
        )

    async def build_context(
        self,
        conversation_id: int | None,
    ) -> dict[str, Any]:
        """Build rich context for decider prompt.

        Returns:
            {
                "conversation_active": bool,
                "conversation_summary": str,  # What we're discussing
                "active_topics": list[str],  # Topics in last 2 turns
                "entities": dict[str, str],  # people, places, things
                "recent_turns": list[dict],  # Last 3 turns with topics
                "recent_transcripts": str,  # Fallback: raw transcripts (current behavior)
            }
        """
        if conversation_id is None:
            # No active conversation
            return {
                "conversation_active": False,
                "conversation_summary": "",
                "active_topics": [],
                "entities": {},
                "recent_turns": [],
                "recent_transcripts": "",
                "recent_actions": self.recent_actions(),
            }

        # Get conversation info
        conv = await self.store.get_active_conversation()
        if conv is None or conv["id"] != conversation_id:
            # Conversation not found or not active
            return {
                "conversation_active": False,
                "conversation_summary": "",
                "active_topics": [],
                "entities": {},
                "recent_turns": [],
                "recent_transcripts": "",
                "recent_actions": self.recent_actions(),
            }

        # Get recent turns
        recent_turns = await self.store.get_recent_turns(conversation_id, n=3)

        # Extract active topics from last 2 turns
        active_topics: list[str] = []
        for turn in recent_turns[-2:]:
            if turn.get("topic_tags"):
                active_topics.extend(turn["topic_tags"])

        # Deduplicate topics
        active_topics = list(dict.fromkeys(active_topics))  # Preserves order

        # Parse entity map
        entities = {}
        if conv.get("entity_map"):
            try:
                entities = json.loads(conv["entity_map"])  # type: ignore[arg-type]
            except (json.JSONDecodeError, TypeError):
                entities = {}

        # Build recent transcripts fallback (for compatibility)
        recent_transcripts = " | ".join([turn["aggregated_text"] for turn in recent_turns])

        return {
            "conversation_active": True,
            "conversation_summary": conv.get("summary") or "",
            "active_topics": active_topics,
            "entities": entities,
            "recent_turns": recent_turns,
            "recent_transcripts": recent_transcripts,
            "recent_actions": self.recent_actions(),
        }

    async def get_or_create_active(self) -> int:
        """Get active conversation or create new one.

        Returns:
            Conversation ID
        """
        # Check for active conversation
        active = await self.store.get_active_conversation()
        if active:
            # Check if less than 30 minutes old
            age = time.time() - active["start_ts"]
            if age <= (30 * 60):  # 30 minutes
                return active["id"]
            else:
                # End old conversation
                await self.store.end_conversation(active["id"])

        # Create new conversation
        from .config import load_settings

        settings = load_settings()
        return await self.store.start_conversation(mode=settings.mode.value)
