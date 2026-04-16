"""Wraps the `claude` CLI for decider + action calls.

Every invocation resumes the same Claude Code session so memory is shared
across decider ticks and action calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from .claude_backend_common import (
    DECISION_KEY_MAP as _DKM,
    DECISION_TYPE_MAP as _DTM,
    extract_decision as _extract_decision_fn,
    normalize_decision_keys as _normalize_decision_keys_fn,
    parse_decider_response,
    strip_markdown_fence as _strip_markdown_fence_fn,
)
from .rate_limit import RateLimiter

if TYPE_CHECKING:
    from .config import Settings


logger = logging.getLogger("heare.claude_cli")


class ClaudeCLIError(RuntimeError):
    pass


class ClaudeCLI:
    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.cwd = settings.workspace_dir
        self.session_file = settings.session_file
        self.binary = settings.claude_cli
        self.timeout = settings.claude_timeout_seconds
        self.max_retries = settings.claude_max_retries
        self.persona: str | None = None
        self._session_id: str | None = None
        self._rate_limiter = RateLimiter(
            max_calls=settings.claude_max_calls_per_minute,
            window_seconds=60.0,
        )

    async def version(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ClaudeCLIError(
                f"claude --version failed: {stderr.decode().strip()}"
            )
        return stdout.decode().strip()

    async def ensure_session(self) -> str | None:
        if self._session_id:
            return self._session_id
        if self.session_file.exists():
            try:
                data = json.loads(self.session_file.read_text())
                sid = data.get("session_id")
                if isinstance(sid, str) and sid:
                    self._session_id = sid
                    return sid
            except json.JSONDecodeError:
                logger.warning("session file corrupt, will bootstrap a new session")
        return None

    def _persist_session(self, session_id: str) -> None:
        if not session_id or session_id == self._session_id:
            self._session_id = session_id
            return
        self._session_id = session_id
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps({"session_id": session_id, "created_at": time.time()})
        )

    def _build_args(
        self, prompt: str, json_output: bool, model: str | None = None
    ) -> list[str]:
        args = [self.binary, "-p", prompt, "--dangerously-skip-permissions"]
        if self._session_id:
            args += ["--resume", self._session_id]
        if json_output:
            args += ["--output-format", "json"]
        if self.persona:
            args += ["--append-system-prompt", self.persona]
        if model:
            args += ["--model", model]
        return args

    async def _read_streams(
        self,
        proc: "asyncio.subprocess.Process",
        on_line: Callable[[str], None] | None,
    ) -> tuple[str, str]:
        """Drain stdout line-by-line (invoking on_line per segment) and
        stderr in full, concurrently, until the process exits.

        Returns the stripped stdout and stderr buffers so _log_invocation
        and return-value consumers stay byte-identical to the prior
        `proc.communicate()` path.
        """
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def drain_stdout() -> None:
            while True:
                try:
                    raw = await proc.stdout.readline()
                except asyncio.LimitOverrunError:
                    # Expected path when a line exceeds StreamReader's buffer
                    # (e.g. a `\r`-based progress bar never emitting `\n`).
                    # Flush a fixed-size chunk so we keep producing events.
                    raw = await proc.stdout.read(8192)
                if not raw:
                    break
                decoded = raw.decode(errors="replace")
                # Progress-bar tools (pytest -v, npm, pip) redraw with `\r`
                # instead of `\n`. Split on both so the dashboard shows
                # visible motion even when the stream has no newlines.
                for segment in decoded.replace("\r", "\n").split("\n"):
                    segment = segment.rstrip("\n")
                    if not segment:
                        continue
                    stdout_chunks.append(segment)
                    if on_line is not None:
                        try:
                            on_line(segment)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("on_line callback raised: %s", e)

        async def drain_stderr() -> None:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                stderr_chunks.append(raw.decode(errors="replace"))

        await asyncio.gather(drain_stdout(), drain_stderr())
        await proc.wait()
        return "\n".join(stdout_chunks).strip(), "".join(stderr_chunks).strip()

    async def _run(
        self,
        prompt: str,
        json_output: bool,
        model: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        await self.ensure_session()
        await self._rate_limiter.acquire()
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            args = self._build_args(prompt, json_output, model)
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=str(self.cwd),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        self._read_streams(proc, on_line),
                        timeout=self.timeout,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    raise ClaudeCLIError(
                        f"claude -p timed out after {self.timeout}s"
                    )
                self._log_invocation(prompt, stdout, stderr, proc.returncode or 0)
                if proc.returncode != 0:
                    if "no conversation found" in stderr.lower() and self._session_id:
                        logger.warning(
                            "session %s stale, bootstrapping fresh",
                            self._session_id,
                        )
                        self._session_id = None
                        continue
                    raise ClaudeCLIError(
                        f"claude -p exited {proc.returncode}: {stderr}"
                    )
                if "context" in stderr.lower() and "limit" in stderr.lower():
                    await self.compact_if_needed(stderr)
                if json_output:
                    self._absorb_session_id(stdout)
                return stdout
            except ClaudeCLIError as e:
                last_error = e
                backoff = 2**attempt
                logger.warning(
                    "claude -p attempt %d failed: %s — retry in %ds",
                    attempt,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
        raise ClaudeCLIError(
            f"claude -p failed after {self.max_retries} attempts: {last_error}"
        )

    def _absorb_session_id(self, stdout: str) -> None:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            sid = payload.get("session_id")
            if isinstance(sid, str) and sid:
                self._persist_session(sid)

    async def __aenter__(self) -> "ClaudeCLI":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def call_decider(self, prompt: str) -> dict[str, Any]:
        raw = await self._run(
            prompt, json_output=True, model=self.settings.claude_decider_model
        )
        return parse_decider_response(raw)

    # Short-key schema aliases — kept for backward compatibility with existing tests
    # that call ClaudeCLI._DECISION_KEY_MAP / _DECISION_TYPE_MAP directly.
    _DECISION_KEY_MAP = _DKM
    _DECISION_TYPE_MAP = _DTM

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        """Alias to shared helper — kept so tests/test_claude_cli.py:208 still passes."""
        return _strip_markdown_fence_fn(text)

    @classmethod
    def _normalize_decision_keys(cls, decision: dict[str, Any]) -> dict[str, Any]:
        """Alias to shared helper — kept so tests/test_claude_cli.py:266 still passes."""
        return _normalize_decision_keys_fn(decision)

    @classmethod
    def _extract_decision(cls, payload: Any) -> Any:
        """Alias to shared helper."""
        return _extract_decision_fn(payload)

    async def call_action(
        self,
        description: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        raw = await self._run(description, json_output=False, on_line=on_line)
        return {"summary": raw}

    async def bootstrap_identity(self, prompt: str) -> dict[str, Any]:
        raw = await self._run(
            prompt, json_output=True, model=self.settings.claude_decider_model
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ClaudeCLIError(f"identity bootstrap returned non-JSON: {e}")
        if isinstance(payload, dict) and "result" in payload:
            result = payload["result"]
            if isinstance(result, str):
                return json.loads(result)
            if isinstance(result, dict):
                return result
        return payload

    async def compact_if_needed(self, error: str) -> bool:
        if "context" not in error.lower() or "limit" not in error.lower():
            return False
        logger.info("context-limit error detected — compacting")
        proc = await asyncio.create_subprocess_exec(
            self.binary,
            "--compact",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0

    def _log_invocation(self, prompt: str, stdout: str, stderr: str, rc: int) -> None:
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.settings.log_dir / f"claude-{int(time.time()*1000)}.log"
        log_file.write_text(
            f"rc={rc}\n--- prompt ---\n{prompt}\n--- stdout ---\n{stdout}\n"
            f"--- stderr ---\n{stderr}\n"
        )
