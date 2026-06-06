"""Run OpenCode CLI as a subprocess and return structured results.

Spawns ``opencode run --format json`` as an async subprocess, consumes
newline-delimited JSON events from stdout, and returns accumulated text
output, cost, tool-call counts, and session information.

One-shot usage::

    result = await run_opencode("list files in /tmp", cwd="/tmp")

Session-continuation usage::

    r1 = await run_opencode("list files", cwd="/tmp")
    sid = r1["session_id"]
    r2 = await run_opencode("sort them by size", cwd="/tmp", session_id=sid)

Standalone utility — does not import from ``src.*`` to avoid circular
imports.  Designed for integration by ``src.agent.tools.direct.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("heare.subagent")



async def run_opencode(
    prompt: str,
    *,
    cwd: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    timeout: float = 120.0,
    binary: str = "opencode",
) -> dict[str, Any]:
    """Run OpenCode CLI as a subprocess, send a prompt, and return structured results.

    Parameters
    ----------
    prompt:
        The prompt to send to OpenCode.
    cwd:
        Working directory for the subprocess (default: current directory).
    model:
        Model override (e.g. ``"opencode-go/deepseek-v4-pro"``).
        When provided the ``--model`` flag is added to the command line.
    session_id:
        OpenCode session ID for continuation.  When provided the
        ``--session`` flag is added.
    timeout:
        Maximum time in *seconds* to wait for the subprocess to complete.
        Default: 120.
    binary:
        Path or name of the ``opencode`` executable.  Default: ``"opencode"``
        (resolved from ``PATH``).

    Returns
    -------
    dict
        Always includes these keys:

        * ``success`` (:class:`bool`) — ``True`` when the subprocess exits
          with code 0 and no error is captured.
        * ``output`` (:class:`str`) — Accumulated text from ``text`` events
          on stdout.  Empty string on failure or if no text was emitted.
        * ``session_id`` (:class:`str`) — OpenCode session ID extracted from
          the first JSON event's ``sessionID`` field.  Empty string if no
          event was received.
        * ``events`` (:class:`list`) — Every JSON event parsed from stdout,
          each a :class:`dict`.  Includes ``step_start``, ``text``,
          ``step_finish``, ``tool_use``, etc.
        * ``cost`` (:class:`float` or ``None``) — Total cost from
          ``step_finish`` events, summed across all steps.  ``None`` if no
          ``step_finish`` event was emitted.
        * ``tokens`` (:class:`dict` or ``None``) — Token-count dict from
          the *last* ``step_finish`` event.  ``None`` if unavailable.
        * ``error`` (:class:`str` or ``None``) — Error message when
          ``success`` is ``False`` (timeout, non-zero exit, exception, …).
        * ``tool_calls`` (:class:`int`) — Count of ``tool_use`` events
          emitted during the run.

    Raises
    ------
    Nothing — all failures are captured in the return dict.
    """
    cmd: list[str] = [binary, "run", "--format", "json", "--dangerously-skip-permissions"]
    if model:
        cmd.extend(["--model", model])
    if session_id:
        cmd.extend(["--session", session_id])
    cmd.append(prompt)

    logger.debug("spawning: %s", cmd)

    result: dict[str, Any] = {
        "success": False,
        "output": "",
        "session_id": "",
        "events": [],
        "cost": None,
        "tokens": None,
        "error": None,
        "tool_calls": 0,
    }

    proc: asyncio.subprocess.Process | None = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        output_parts: list[str] = []
        events: list[dict[str, Any]] = []
        cost: float | None = None
        tokens: dict[str, Any] | None = None
        tool_calls: int = 0
        session_id_out: str = ""

        if proc.stdout is None:
            result["error"] = "Subprocess has no stdout pipe"
            return result

        async for line in proc.stdout:
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                event: dict[str, Any] = json.loads(line_str)
            except json.JSONDecodeError:
                logger.debug(
                    "non-JSON stdout line from opencode: %s", line_str[:200]
                )
                continue

            events.append(event)

            if not session_id_out:
                session_id_out = event.get("sessionID", "")

            etype = event.get("type", "")

            if etype == "text":
                text = event.get("part", {}).get("text", "")
                if text is None:
                    text = ""
                output_parts.append(str(text))

            elif etype == "step_finish":
                part = event.get("part", {})
                if part:
                    step_cost = part.get("cost")
                    step_tokens = part.get("tokens")
                    if step_cost is not None:
                        cost = (cost or 0.0) + float(step_cost)
                    if step_tokens is not None:
                        tokens = step_tokens

            elif etype == "tool_use":
                tool_calls += 1

        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "opencode timed out after %.1fs — sending SIGTERM", timeout
            )
            _terminate_graceful(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "opencode did not exit after SIGTERM — sending SIGKILL"
                )
                _kill_forceful(proc)
                await proc.wait()

            _fill_result(
                result,
                output_parts,
                events,
                session_id_out,
                cost,
                tokens,
                tool_calls,
                error=f"Timed out after {timeout}s",
            )
            return result

        stderr_str = ""
        if proc.stderr is not None:
            stderr_bytes = await proc.stderr.read()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

        if proc.returncode == 0:
            _fill_result(
                result,
                output_parts,
                events,
                session_id_out,
                cost,
                tokens,
                tool_calls,
                success=True,
            )
        else:
            error_msg = stderr_str or f"Exit code {proc.returncode}"
            _fill_result(
                result,
                output_parts,
                events,
                session_id_out,
                cost,
                tokens,
                tool_calls,
                error=error_msg,
            )

    except FileNotFoundError:
        result["error"] = f"OpenCode binary not found: {binary}"
    except Exception as exc:
        logger.exception("OpenCode subprocess raised")
        result["error"] = str(exc)

    return result


def _fill_result(
    result: dict[str, Any],
    output_parts: list[str],
    events: list[dict[str, Any]],
    session_id_out: str,
    cost: float | None,
    tokens: dict[str, Any] | None,
    tool_calls: int,
    *,
    success: bool = False,
    error: str | None = None,
) -> None:
    result["success"] = success
    result["output"] = "".join(output_parts)
    result["session_id"] = session_id_out
    result["events"] = events
    result["cost"] = cost
    result["tokens"] = tokens
    result["error"] = error
    result["tool_calls"] = tool_calls


def _terminate_graceful(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        pass


def _kill_forceful(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass


__all__ = ["run_opencode"]
