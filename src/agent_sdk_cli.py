"""Persistent Claude backend using claude-agent-sdk.

Replaces per-call `claude -p` subprocess spawning with a single long-running
ClaudeSDKClient session. Set `use_agent_sdk = true` in ~/.heare/config.toml
to activate; the subprocess backend (ClaudeCLI) remains the default.

Usage:
    async with AgentSDKCLI(settings) as sdk_cli:
        decision = await sdk_cli.call_decider(prompt)
        result = await sdk_cli.call_action(description, on_line=callback)

Tool access: actions use allowed_tools=["Bash"] (plus computer tools once the
correct SDK identifier is confirmed). The decider uses the same shared client;
the decider prompt template is responsible for keeping decider ticks text-only.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    UserMessage,
)

from .claude_backend_common import extract_decision, parse_decider_response, strip_markdown_fence
from .rate_limit import RateLimiter

if TYPE_CHECKING:
    from .config import Settings


logger = logging.getLogger("heare.agent_sdk_cli")


class ClaudeCLIError(RuntimeError):
    pass


def _extract_tool_result_text(content: Any) -> str:
    """Flatten a ToolResultBlock.content into plain text.

    content can be str, list[dict], or None (see claude_agent_sdk.ToolResultBlock).
    Dict items may carry {"type": "text", "text": "..."}; unknown shapes are
    repr'd so caller still sees *something* rather than silently losing output.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_val = item.get("text")
                if isinstance(text_val, str):
                    parts.append(text_val)
                    continue
                parts.append(repr(item))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(repr(item))
        return "\n".join(p for p in parts if p)
    return repr(content)


class AgentSDKCLI:
    """Persistent Claude backend using the claude-agent-sdk Python package.

    Lifecycle: use as an async context manager. __aenter__ loads the session
    and opens ClaudeSDKClient; __aexit__ closes it. The underlying Node.js
    process stays alive across all calls, eliminating per-call startup overhead.

    Note on serialization: _run_query holds an asyncio.Lock so concurrent
    callers (ActionWorker, ConversationManager.extract_topics, identity
    bootstrap) don't scramble each other's receive_response() streams on
    the shared ClaudeSDKClient. Phase D will split into two clients for
    parallelism; until then, the lock trades a little latency for
    correctness.

    Note on persona: Claude Code appends persona text via --append-system-prompt.
    The SDK's system_prompt field replaces rather than appends, which would break
    Claude Code's built-in behaviors. Persona injection is therefore deferred to
    a follow-up; the SDK path runs without a custom persona in the current release.
    """

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.cwd = settings.workspace_dir
        self.session_file = settings.session_file
        self.timeout = settings.claude_timeout_seconds
        self.max_retries = settings.claude_max_retries
        self.persona: str | None = None
        self._session_id: str | None = None
        self._rate_limiter = RateLimiter(
            max_calls=settings.claude_max_calls_per_minute,
            window_seconds=60.0,
        )
        self._client: ClaudeSDKClient | None = None
        self._client_lock = asyncio.Lock()  # serialize reconnects only
        # Serialize _run_query to prevent concurrent query()/receive_response()
        # pairs from scrambling each other's streams. Without this, an in-flight
        # action and a background topic-extraction call share the same
        # ClaudeSDKClient and their messages interleave, causing things like a
        # topic-JSON array to appear inside an action summary.
        self._run_query_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AgentSDKCLI":
        self._load_session_id_from_file()
        await self._open_client()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._close_client()

    def _load_session_id_from_file(self) -> None:
        if self._session_id:
            return
        if not self.session_file.exists():
            return
        try:
            data = json.loads(self.session_file.read_text())
            sid = data.get("session_id")
            if isinstance(sid, str) and sid:
                self._session_id = sid
        except (OSError, json.JSONDecodeError):
            logger.warning("session file corrupt, starting fresh")

    def _persist_session(self, session_id: str) -> None:
        if not session_id:
            return
        if session_id == self._session_id:
            return
        self._session_id = session_id
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps({"session_id": session_id, "created_at": time.time()})
        )

    async def _open_client(self) -> None:
        """Open a new ClaudeSDKClient with current session state."""
        # Derive allowed MCP tool patterns from workspace/.mcp.json — the
        # single source of truth. No separate enable_mcp_servers allowlist.
        from .mcp_utils import build_mcp_allowed_patterns, read_mcp_servers

        mcp_servers = read_mcp_servers(self.settings.workspace_dir)
        allowed = list(
            set(self.settings.get_sdk_allowed_tools()) | set(build_mcp_allowed_patterns(mcp_servers))
        )
        options = ClaudeAgentOptions(
            allowed_tools=allowed,
            resume=self._session_id,
            cwd=str(self.cwd),
            cli_path=self.settings.claude_sdk_cli_path or self.settings.claude_cli,
            permission_mode="bypassPermissions",
        )
        self._client = ClaudeSDKClient(options)
        await self._client.__aenter__()

    async def _close_client(self) -> None:
        """Close the SDK client idempotently."""
        async with self._client_lock:
            if self._client is not None:
                try:
                    await self._client.__aexit__(None, None, None)
                except Exception:
                    logger.warning("error closing SDK client", exc_info=True)
                finally:
                    self._client = None

    async def _reconnect(self) -> None:
        """Close the current client, clear session, and reopen."""
        async with self._client_lock:
            if self._client is not None:
                try:
                    await self._client.__aexit__(None, None, None)
                except Exception:
                    pass
                self._client = None
            self._session_id = None
            await self._open_client()

    # ------------------------------------------------------------------
    # version
    # ------------------------------------------------------------------

    async def version(self) -> str:
        try:
            v = importlib.metadata.version("claude-agent-sdk")
        except importlib.metadata.PackageNotFoundError:
            v = "unknown"
        return f"claude-agent-sdk/{v}"

    # ------------------------------------------------------------------
    # Core query runner
    # ------------------------------------------------------------------

    async def _run_query(
        self,
        prompt: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        """Send prompt, iterate response, return joined stdout text.

        Handles rate limiting, retry/backoff, timeout, stale-session recovery,
        and per-invocation log files. Serialized via _run_query_lock so
        concurrent callers (action worker + background topic extraction) do
        not scramble each other's streams on the shared SDK client.
        """
        async with self._run_query_lock:
            await self._rate_limiter.acquire()
            last_error: Exception | None = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    return await self._attempt_query(prompt, on_line=on_line)
                except ClaudeCLIError as e:
                    msg = str(e).lower()
                    is_session_error = (
                        "no conversation found" in msg
                        or ("session" in msg and "not found" in msg)
                        or "sdk result error" in msg  # opaque SDK errors are often stale sessions
                    )
                    if is_session_error and attempt == 1:
                        logger.warning(
                            "stale session on attempt %d — reconnecting: %s", attempt, e
                        )
                        await self._reconnect()
                        last_error = e
                        continue
                    last_error = e
                    backoff = 2 ** attempt
                    logger.warning(
                        "SDK attempt %d failed: %s — retry in %ds", attempt, e, backoff
                    )
                    await asyncio.sleep(backoff)
            raise ClaudeCLIError(
                f"claude-agent-sdk failed after {self.max_retries} attempts: {last_error}"
            )

    async def _attempt_query(
        self,
        prompt: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        if self._client is None:
            raise ClaudeCLIError("SDK client not initialized — call __aenter__ first")

        await self._client.query(prompt)
        # Phase AH2-03: collect assistant text and tool-result bodies in
        # separate buckets. When Claude emits a text summary we prefer it
        # (avoids duplicating raw stdout alongside the summary). When Claude
        # ends the turn silently we fall back to the tool-result content so
        # the caller still has *something* to speak.
        text_chunks: list[str] = []
        tool_result_chunks: list[str] = []
        result_session_id: str | None = None
        iterator = self._client.receive_response()
        # Diagnostic counters so we can see how many text/tool-result blocks
        # the SDK actually delivered per call. Empty assistant text + empty
        # tool-result usually means Claude ran the tool and ended the turn
        # silently — the caller should force a summary via the prompt.
        block_counts: dict[str, int] = {"text": 0, "tool_result": 0}

        def _emit_segments(text: str, bucket: list[str]) -> None:
            for segment in text.replace("\r", "\n").split("\n"):
                if not segment:
                    continue
                bucket.append(segment)
                if on_line is not None:
                    try:
                        on_line(segment)
                    except Exception as e:
                        logger.warning("on_line callback raised: %s", e)

        async def _drain() -> None:
            nonlocal result_session_id
            async for message in iterator:
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            block_counts["text"] += 1
                            _emit_segments(block.text, text_chunks)
                elif isinstance(message, UserMessage):
                    content = message.content
                    blocks = content if isinstance(content, list) else []
                    for block in blocks:
                        if isinstance(block, ToolResultBlock):
                            block_counts["tool_result"] += 1
                            text = _extract_tool_result_text(block.content)
                            if text:
                                _emit_segments(text, tool_result_chunks)
                elif isinstance(message, ResultMessage):
                    if message.session_id:
                        result_session_id = message.session_id
                    if message.is_error:
                        detail = (
                            '; '.join(message.errors or [])
                            or message.result
                            or f"subtype={message.subtype} stop_reason={message.stop_reason}"
                        )
                        raise ClaudeCLIError(f"SDK result error: {detail}")

        try:
            await asyncio.wait_for(_drain(), timeout=self.timeout)
        except asyncio.TimeoutError:
            # Explicit cleanup to avoid leaking the persistent Node.js process.
            try:
                await iterator.aclose()
            except Exception:
                pass
            raise ClaudeCLIError(
                f"claude-agent-sdk timed out after {self.timeout}s"
            )

        if result_session_id:
            self._persist_session(result_session_id)

        # Prefer Claude's text summary; fall back to raw tool-result content
        # only when Claude did not emit any text this turn.
        chosen = text_chunks if text_chunks else tool_result_chunks
        stdout = "\n".join(chosen).strip()
        logger.info(
            "[SDK DRAIN text=%d tool_result=%d source=%s stdout_len=%d]",
            block_counts["text"],
            block_counts["tool_result"],
            "text" if text_chunks else "tool_result" if tool_result_chunks else "none",
            len(stdout),
        )
        self._log_invocation(prompt, stdout)
        return stdout

    # ------------------------------------------------------------------
    # Public API (same surface as ClaudeCLI)
    # ------------------------------------------------------------------

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        raw = await self._run_query(prompt)
        return parse_decider_response(raw)

    async def call_action(
        self,
        description: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        raw = await self._run_query(description, on_line=on_line)
        return {"summary": raw}

    async def bootstrap_identity(self, prompt: str) -> dict[str, Any]:
        """One-off startup call — uses extract_decision only (no key normalization)."""
        raw = await self._run_query(prompt)
        try:
            payload = json.loads(strip_markdown_fence(raw))
        except json.JSONDecodeError as e:
            raise ClaudeCLIError(f"identity bootstrap returned non-JSON: {e}")
        result = extract_decision(payload)
        if isinstance(result, str):
            return json.loads(result)
        if isinstance(result, dict):
            return result
        return payload

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_invocation(self, prompt: str, stdout: str) -> None:
        try:
            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            log_file = self.settings.log_dir / f"claude-{int(time.time() * 1000)}.log"
            log_file.write_text(
                f"rc=0\n--- prompt ---\n{prompt}\n--- stdout ---\n{stdout}\n"
                f"--- stderr ---\n(sdk path — no stderr)\n"
            )
        except Exception:
            logger.warning("failed to write invocation log", exc_info=True)
