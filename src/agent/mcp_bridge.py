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
import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
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

# ``ToolDef.handler`` is a dispatch key for built-ins living in
# ``tools/direct.py``. MCP tools have no entry there and must not: they
# are dispatched by name through a live session (see ``McpBridge.call``).
# The marker exists so anything reading the registry can tell them apart.
_MCP_HANDLER = "mcp"

_mcp_tool_def_cls: Any = None

# The one file that decides which servers exist. ``settings.mcp_dir`` is
# what this bridge, the capability index and the installer all read; the
# older copy under ``workspace_dir`` is migrated away by
# ``ensure_mcp_config`` at boot and is read by nothing at run time.
MCP_CONFIG_FILE = ".mcp.json"

_DEFAULT_MCP_DIR = Path.home() / ".heare" / "mcp"


def mcp_config_path(settings: Any) -> Path:
    """Absolute path of the ``.mcp.json`` this process actually obeys."""
    mcp_dir = getattr(settings, "mcp_dir", None) or _DEFAULT_MCP_DIR
    return Path(mcp_dir).expanduser() / MCP_CONFIG_FILE


def load_mcp_config(path: Path) -> dict[str, dict] | None:
    """Read ``.mcp.json`` strictly: ``None`` means "do not act on this".

    ``read_mcp_servers`` answers "no servers" to a missing file, invalid
    JSON and an empty config alike — right for a cold boot, wrong for a
    live reload, where the same answer would tear down every working
    server because someone saved a file with a trailing comma. Here the
    three cases stay apart: ``None`` for missing/unreadable/malformed
    (caller keeps what it has), ``{}`` only when the file genuinely says
    there are no servers.
    """
    try:
        raw = Path(path).read_text("utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    return servers


def enabled_servers(servers: dict[str, dict]) -> list[str]:
    """Slugs the bridge would try to connect — the same filter ``connect`` uses."""
    return [
        slug
        for slug, entry in servers.items()
        if isinstance(entry, dict) and not entry.get("disabled")
    ]


def _mcp_tool_def(
    *,
    name: str,
    description: str,
    handler: str,
    schema_fields: dict,
    required: list[str],
) -> Any:
    """A ``ToolDef`` that keeps optional MCP arguments optional.

    ``ToolDef.__post_init__`` treats an empty ``required`` as "everything
    is required", which is right for the hand-written built-ins and wrong
    for a server-supplied JSON Schema — a tool with five optional
    arguments would be advertised as needing all five. The subclass drops
    that rule and takes the server's list verbatim.
    """
    global _mcp_tool_def_cls
    if _mcp_tool_def_cls is None:
        from src.agent.tools.system import ToolDef

        class _McpToolDef(ToolDef):  # type: ignore[misc, valid-type]
            def __post_init__(self) -> None:
                return None

        _mcp_tool_def_cls = _McpToolDef
    return _mcp_tool_def_cls(
        name=name,
        description=description,
        handler=handler,
        schema_fields=schema_fields,
        required=required,
    )


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
        wanted = enabled_servers(servers)
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

    # -- the worker's path ---------------------------------------------
    #
    # Everything above this line speaks Pipecat: ``FunctionSchema`` and
    # ``llm.register_function``. The spine has neither. Its worker
    # (``src/agent/hands.py``) builds its schema list from the plain
    # ``ToolDef`` registry in ``src/agent/tools/system.py`` and calls
    # tools by name, so an MCP tool reaches it only by being *in that
    # list*. The two methods below are that path, and nothing else in
    # the tree provided it.

    def register_worker_tools(self) -> list[str]:
        """Publish every connected MCP tool into the shared ToolDef list.

        ``src.agent.tools.system.TOOLS`` is a module-level list, so one
        append makes the tool visible to every ``Hands`` instance in the
        process — including ones built before the bridge connected. The
        worker's own mode gate then filters the names, which is why a
        role denying ``mcp__*`` keeps working with no extra wiring.

        Idempotent: a second call replaces what the first one added.
        """
        from src.agent.tools.system import TOOLS

        self.unregister_worker_tools()
        added: list[str] = []
        for fn_name, description, schema, _session in self._tools:
            props: dict = {}
            required: list[str] = []
            if isinstance(schema, dict):
                props = schema.get("properties") or {}
                required = list(schema.get("required") or [])
            TOOLS.append(
                _mcp_tool_def(
                    name=fn_name,
                    description=description,
                    handler=_MCP_HANDLER,
                    schema_fields=props,
                    required=required,
                )
            )
            added.append(fn_name)
        if added:
            logger.info(
                "mcp: %d tool(s) registered for the worker: %s",
                len(added),
                ", ".join(added),
            )
        return added

    @staticmethod
    def unregister_worker_tools() -> None:
        """Take the MCP tools back out of the shared registry.

        Without this a torn-down bridge leaves schemas the worker would
        offer and then fail to call — and a test that connects a fake
        bridge would leak its tools into every test after it.
        """
        from src.agent.tools.system import TOOLS

        TOOLS[:] = [t for t in TOOLS if not t.name.startswith("mcp__")]

    async def call(self, fn_name: str, arguments: dict | None = None) -> dict[str, Any]:
        """Call one MCP tool by its ``mcp__<slug>__<tool>`` name.

        Framework-free counterpart of ``_make_handler``: returns the
        ``{"success", "output", ...}`` dict directly instead of pushing it
        through a Pipecat result callback. Never raises except on
        cancellation.
        """
        session = None
        for name, _desc, _schema, sess in self._tools:
            if name == fn_name:
                session = sess
                break
        if session is None:
            return {
                "success": False,
                "output": "",
                "error": f"{fn_name}: no connected MCP server offers this tool",
            }
        tool_name = fn_name.split("__", 2)[2]
        try:
            raw = await session.call_tool(tool_name, dict(arguments or {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a tool failure is data
            logger.exception("mcp_bridge: %r raised", fn_name)
            return {
                "success": False,
                "output": "",
                "error": f"{fn_name} error: {exc!s}",
            }
        return _normalise_call_result(raw)

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

    async def aclose(self, *, unregister: bool = True) -> None:
        """Stop every server this bridge owns.

        ``unregister=False`` is for a hot reload: the replacement bridge
        has already put its own tools in the shared registry, and
        ``unregister_worker_tools`` removes *every* ``mcp__*`` entry
        there — including the new ones, whose names usually repeat the
        old ones. Closing the dead bridge must not empty the live set.
        """
        # First stop advertising what is about to stop working.
        if unregister:
            try:
                self.unregister_worker_tools()
            except Exception:  # noqa: BLE001 — shutdown best-effort
                logger.exception("mcp_bridge: unregister failed (non-fatal)")
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


__all__ = [
    "McpBridge",
    "connect_mcp_servers",
    "enabled_servers",
    "load_mcp_config",
    "mcp_config_path",
]
