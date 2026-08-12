"""Stdio MCP client bridge for the voice pipeline.

Until now ``.mcp.json`` servers were only *described* to the
LLM (prompt text + capability index) — there was no client that could
actually call them, so ``mcp__<slug>__*`` tool names were unbacked.

This module closes that gap: on daemon start it spawns every enabled
stdio MCP server, lists its tools, and exposes them as Pipecat
``FunctionSchema`` + ``register_function`` handlers using the exact
same chokepoint built-in tools use (``src/agent/tools/schemas.py``).
A handler simply forwards to ``session.call_tool`` and normalises the
``CallToolResult`` into the ``{"success", "output", "error"}`` dict
shape the rest of heare expects.

Design notes:
* One slow or crashing server must never block daemon startup or kill
  the others — every connect is per-server, timeboxed, and best-effort.
* All sessions/processes are owned by a single ``AsyncExitStack`` so
  shutdown is one ``aclose()`` call wired into main.py's finally block.
* ``env`` is merged onto ``os.environ`` so ``npx``/``node`` resolve on
  PATH the same way they do for a user shell.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Callable

from src.skills.mcp_utils import read_mcp_servers

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger("heare.mcp_bridge")

# A server that hangs on initialize/list_tools must not wedge startup.
# macos-use's first launch runs a Swift release build via npm postinstall
# and can exceed this — pre-warm it once (`npx -y mcp-server-macos-use`)
# or it is simply skipped this boot and picked up on the next restart.
_CONNECT_TIMEOUT_S: float = 60.0

_mcp_intent_seq = itertools.count(start=1)


def _normalise_call_result(result: Any) -> dict[str, Any]:
    """Convert an MCP ``CallToolResult`` into heare's tool-result dict.

    Text content blocks are concatenated; a structured payload (if the
    server returned one) is passed through under ``structured`` so the
    LLM summary path can still see it.
    """
    is_error = bool(getattr(result, "isError", False))
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    output = "\n".join(parts).strip()
    payload: dict[str, Any] = {
        "success": not is_error,
        "output": output,
    }
    structured = getattr(result, "structuredContent", None)
    if structured:
        payload["structured"] = structured
    if is_error:
        payload["error"] = output or "MCP tool returned an error"
    return payload


class McpBridge:
    """Owns live MCP sessions and exposes their tools to the pipeline."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        # (function_name, description, input_schema, session)
        self._tools: list[tuple[str, str, dict, Any]] = []
        self._connected_servers: list[str] = []

    @property
    def connected_servers(self) -> list[str]:
        return list(self._connected_servers)

    @property
    def tool_names(self) -> list[str]:
        return [t[0] for t in self._tools]

    async def _connect_one(self, slug: str, entry: dict) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = entry.get("command")
        if not command:
            logger.warning("mcp_bridge: server %r has no command; skipping", slug)
            return
        env = {**os.environ, **(entry.get("env") or {})}
        params = StdioServerParameters(
            command=command,
            args=list(entry.get("args") or []),
            env=env,
        )

        async def _open() -> Any:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session

        session = await asyncio.wait_for(_open(), timeout=_CONNECT_TIMEOUT_S)
        listed = await asyncio.wait_for(
            session.list_tools(), timeout=_CONNECT_TIMEOUT_S
        )
        count = 0
        for tool in listed.tools:
            fn_name = f"mcp__{slug}__{tool.name}"
            schema = tool.inputSchema or {"type": "object", "properties": {}}
            self._tools.append((fn_name, tool.description or fn_name, schema, session))
            count += 1
        self._connected_servers.append(slug)
        logger.info(
            "mcp_bridge: connected %r (%d tools: %s)",
            slug,
            count,
            ", ".join(t.name for t in listed.tools) or "none",
        )

    async def connect(self, settings: "Settings") -> None:
        """Connect every enabled server, and say plainly what happened.

        This used to report nothing at all on the happy path, so silence
        meant both "connected fine" and "could not connect to anything".
        The daemon log holds not one line about MCP across the whole
        history of the project — during which no server ever connected.
        """
        servers = read_mcp_servers(settings.mcp_dir)
        wanted = [
            slug
            for slug, entry in servers.items()
            if isinstance(entry, dict) and not entry.get("disabled")
        ]
        for slug in servers:
            if slug not in wanted:
                logger.debug("mcp: %r disabled or invalid; skipping", slug)

        if not wanted:
            logger.info("mcp: no servers configured in %s", settings.mcp_dir)
            return

        # Checked once, before the loop. A missing client is an install
        # problem that affects every server, not a server that failed to
        # spawn — caught per-server it read as "that one is broken", once
        # per entry, and the real cause never appeared in the summary.
        try:
            import mcp  # noqa: F401
        except ImportError:
            logger.error(
                "mcp: the client library is not installed, so none of the %d "
                "configured server(s) can connect — add mcp>=1.11.0,<2 to the "
                "dependencies",
                len(wanted),
            )
            return

        failed: list[str] = []
        for slug in wanted:
            try:
                await self._connect_one(slug, servers[slug])
            except asyncio.TimeoutError:
                failed.append(slug)
                logger.warning(
                    "mcp: %r timed out after %.0fs; skipping this boot",
                    slug,
                    _CONNECT_TIMEOUT_S,
                )
            except Exception:  # noqa: BLE001 — one bad server must not break boot
                failed.append(slug)
                logger.exception("mcp: %r failed to connect; skipping", slug)

        if failed:
            logger.warning(
                "mcp: %d of %d server(s) connected — failed: %s",
                len(self._connected_servers),
                len(wanted),
                ", ".join(failed),
            )
        else:
            logger.info(
                "mcp: %d server(s) connected, %d tool(s) — %s",
                len(self._connected_servers),
                len(self._tools),
                ", ".join(self._connected_servers),
            )

    def prompt_block(self) -> str:
        """Live system-prompt block listing what is actually callable.

        Replaces the static name-only block built from .mcp.json text: it
        names every connected server and its real ``mcp__slug__tool``
        function names, and states plainly they are ready now — so the
        model stops hedging ("maybe needs a restart") about tools it can
        already call. Empty string when nothing connected.
        """
        if not self._tools:
            return ""
        by_server: dict[str, list[str]] = {}
        for fn_name, _desc, _schema, _session in self._tools:
            # fn_name == mcp__<slug>__<tool>
            slug = fn_name.split("__", 2)[1]
            by_server.setdefault(slug, []).append(fn_name)
        lines = [
            f"Connected MCP servers ({len(by_server)}) — these tools are "
            "registered and callable RIGHT NOW. Call them directly; never "
            "say a connected server needs a restart or is not configured:"
        ]
        for slug, fns in by_server.items():
            lines.append(f"  - {slug} ({len(fns)} tools): {', '.join(fns)}")
        return "\n".join(lines)

    def function_schemas(self) -> list[Any]:
        """Pipecat ``FunctionSchema`` objects for every connected MCP tool."""
        from pipecat.adapters.schemas.function_schema import FunctionSchema

        schemas: list[Any] = []
        for fn_name, description, schema, _session in self._tools:
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            required = schema.get("required", []) if isinstance(schema, dict) else []
            schemas.append(
                FunctionSchema(
                    name=fn_name,
                    description=description,
                    properties=props,
                    required=required,
                )
            )
        return schemas

    def register(
        self,
        llm: Any,
        conversation_manager: Any = None,
        session_state: Any = None,
    ) -> list[str]:
        """Register one ``register_function`` handler per MCP tool."""
        registered: list[str] = []
        for fn_name, _description, _schema, session in self._tools:
            handler = self._make_handler(
                fn_name, session, conversation_manager, session_state
            )
            llm.register_function(fn_name, handler, cancel_on_interruption=True)
            registered.append(fn_name)
        return registered

    @staticmethod
    def _make_handler(
        fn_name: str,
        session: Any,
        conversation_manager: Any,
        session_state: Any = None,
    ) -> Callable[[Any], Any]:
        # mcp__<slug>__<tool> → bare tool name the server expects.
        tool_name = fn_name.split("__", 2)[2]

        async def handler(params: Any) -> None:
            args = dict(params.arguments or {})
            intent_id = next(_mcp_intent_seq)
            # Gate on the full mcp__slug__tool name so mode globs like
            # "mcp__*" / "macos-use*" match as intended.
            from src.agent.modes import mode_gate_refusal

            refusal = mode_gate_refusal(session_state, fn_name)
            if refusal is not None:
                if conversation_manager is not None:
                    try:
                        conversation_manager.record_action_pending(
                            intent_id, fn_name, str(args)
                        )
                        conversation_manager.record_action_error(
                            intent_id, refusal["error"]
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("mcp_bridge: mode_gate action-log failed")
                await params.result_callback(refusal)
                return
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_pending(
                        intent_id, fn_name, str(args)
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "mcp_bridge: record_action_pending failed (non-fatal)"
                    )
            try:
                raw = await session.call_tool(tool_name, args)
                result = _normalise_call_result(raw)
            except asyncio.CancelledError:
                if conversation_manager is not None:
                    try:
                        conversation_manager.record_action_cancelled(
                            intent_id, tool=fn_name, args=str(args)
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("mcp_bridge: record_action_cancelled failed")
                raise
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.exception("mcp_bridge: %r raised", fn_name)
                if conversation_manager is not None:
                    try:
                        conversation_manager.record_action_error(intent_id, repr(exc))
                    except Exception:  # noqa: BLE001
                        logger.exception("mcp_bridge: record_action_error failed")
                await params.result_callback(
                    {
                        "success": False,
                        "output": "",
                        "error": f"{fn_name} error: {exc!s}",
                    }
                )
                return
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_result(
                        intent_id, result.get("output", "")
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "mcp_bridge: record_action_result failed (non-fatal)"
                    )
            await params.result_callback(result)

        handler.__name__ = f"_handle_{fn_name}"
        return handler

    async def aclose(self) -> None:
        try:
            await self._stack.aclose()
        except Exception:  # noqa: BLE001 — shutdown best-effort
            logger.exception("mcp_bridge: aclose failed (non-fatal)")


async def connect_mcp_servers(settings: "Settings") -> McpBridge:
    """Build and connect an :class:`McpBridge` from ``~/.heare/mcp/.mcp.json``.

    Always returns a bridge (possibly empty); never raises so a bad MCP
    config cannot stop the daemon from coming up.
    """
    bridge = McpBridge()
    try:
        await bridge.connect(settings)
    except Exception:  # noqa: BLE001 — defensive top-level guard
        logger.exception("mcp_bridge: connect_mcp_servers failed (non-fatal)")
    return bridge


__all__ = ["McpBridge", "connect_mcp_servers"]
