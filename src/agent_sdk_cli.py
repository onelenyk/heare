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
)

from .claude_backend_common import extract_decision, parse_decider_response, strip_markdown_fence
from .rate_limit import RateLimiter

if TYPE_CHECKING:
    from .config import Settings


logger = logging.getLogger("heare.agent_sdk_cli")


class ClaudeCLIError(RuntimeError):
    pass


class AgentSDKCLI:
    """Persistent Claude backend using the claude-agent-sdk Python package.

    Lifecycle: use as an async context manager. __aenter__ loads the session
    and opens ClaudeSDKClient; __aexit__ closes it. The underlying Node.js
    process stays alive across all calls, eliminating per-call startup overhead.

    Note on serialization: the DeciderProcessor FSM (decider.py:506) serializes
    all call_decider and call_action calls — they are structurally mutually
    exclusive. No additional call-level lock is needed here.

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
        options = ClaudeAgentOptions(
            allowed_tools=["Bash"],
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
        and per-invocation log files.
        """
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
        stdout_chunks: list[str] = []
        result_session_id: str | None = None
        iterator = self._client.receive_response()

        async def _drain() -> None:
            nonlocal result_session_id
            async for message in iterator:
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            for segment in block.text.replace("\r", "\n").split("\n"):
                                if not segment:
                                    continue
                                stdout_chunks.append(segment)
                                if on_line is not None:
                                    try:
                                        on_line(segment)
                                    except Exception as e:
                                        logger.warning("on_line callback raised: %s", e)
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

        stdout = "\n".join(stdout_chunks).strip()
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
