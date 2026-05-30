"""LLM tool registration — Pipecat ``register_function`` bridge.

This module connects Pipecat's ``register_function`` API to heare's
``execute_direct`` dispatcher. Each enabled tool in the registry gets:

* a ``FunctionSchema`` describing its arguments for the LLM, and
* an async ``FunctionCallParams``-shaped handler that maps the LLM's
  structured arguments back into the string-form ``args`` that
  ``execute_direct`` expects.

When a ``conversation_manager`` is supplied to
:func:`register_all_tools`, every tool invocation is bracketed with
``record_action_pending`` / ``record_action_result`` /
``record_action_cancelled`` / ``record_action_error`` so the
next-turn LLM context's recent-actions block stays populated and the
model has the grounding it needs to avoid duplicate searches.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from src.agent.tools.direct import execute_direct
from src.agent.tools.registry import TOOLS, get_enabled_tools, get_tool

if TYPE_CHECKING:
    from src.config import Settings


logger = logging.getLogger("heare.llm_tools")


# ---------------------------------------------------------------------------
# Per-tool argument schemas
#
# Each entry maps a tool name (the registry key) to a tuple
# ``(properties, required, args_serializer)`` where ``args_serializer``
# converts the structured ``arguments`` dict the LLM emits back into the
# string form ``execute_direct`` expects. This split lets us hand the
# LLM a typed schema while keeping the legacy dispatcher unchanged.
# ---------------------------------------------------------------------------

ArgsSerializer = Callable[[dict[str, Any]], str]


def _bash_args(args: dict[str, Any]) -> str:
    return str(args.get("command", "")).strip()


def _path_args(args: dict[str, Any]) -> str:
    return str(args.get("path", "")).strip()


def _write_args(args: dict[str, Any]) -> str:
    """write expects 'filepath: content' on legacy path."""
    path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    return f"{path}: {content}"


def _url_args(args: dict[str, Any]) -> str:
    return str(args.get("url", "")).strip()


def _query_args(args: dict[str, Any]) -> str:
    return str(args.get("query", "")).strip()


def _name_args(args: dict[str, Any]) -> str:
    return str(args.get("name", "")).strip()


def _provider_args(args: dict[str, Any]) -> str:
    return str(args.get("provider", "")).strip()


def _mode_args(args: dict[str, Any]) -> str:
    return str(args.get("mode", "")).strip()


def _display_args(args: dict[str, Any]) -> str:
    # content can be long and contain newlines → JSON blob.
    return json.dumps(args)


def _id_args(args: dict[str, Any]) -> str:
    return str(args.get("speaker_id", args.get("id", ""))).strip()


def _rename_args(args: dict[str, Any]) -> str:
    sid = str(args.get("speaker_id", "")).strip()
    new = str(args.get("new_name", "")).strip()
    # Legacy execute_direct expects "id new_name".
    return f"{sid} {new}".strip()


def _empty_args(_args: dict[str, Any]) -> str:
    return ""


def _workflow_args(args: dict[str, Any]) -> str:
    return str(args.get("name", "")).strip()


def _json_args(args: dict[str, Any]) -> str:
    """Pass through arguments as JSON string for tool creation."""
    return json.dumps(args)


_TOOL_SPECS: dict[str, tuple[dict[str, Any], list[str], ArgsSerializer]] = {
    "bash": (
        {
            "command": {
                "type": "string",
                "description": "The shell command to execute in the workspace.",
            }
        },
        ["command"],
        _bash_args,
    ),
    "read": (
        {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            }
        },
        ["path"],
        _path_args,
    ),
    "write": (
        {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to create or overwrite.",
            },
            "content": {
                "type": "string",
                "description": "The full file contents to write.",
            },
        },
        ["path", "content"],
        _write_args,
    ),
    "web_fetch": (
        {
            "url": {
                "type": "string",
                "description": "URL to fetch and return the content of.",
            }
        },
        ["url"],
        _url_args,
    ),
    "web_search": (
        {
            "query": {
                "type": "string",
                "description": "Search query.",
            }
        },
        ["query"],
        _query_args,
    ),
    "workflow": (
        {
            "name": {
                "type": "string",
                "description": "Workflow name to execute.",
            }
        },
        ["name"],
        _workflow_args,
    ),
    "re_enroll": (
        {},
        [],
        _empty_args,
    ),
    "list_profiles": (
        {},
        [],
        _empty_args,
    ),
    "create_profile": (
        {
            "name": {
                "type": "string",
                "description": "Display name for the new voice profile.",
            }
        },
        ["name"],
        _name_args,
    ),
    "delete_profile": (
        {
            "speaker_id": {
                "type": "string",
                "description": "ID of the speaker profile to delete.",
            }
        },
        ["speaker_id"],
        _id_args,
    ),
    "rename_profile": (
        {
            "speaker_id": {
                "type": "string",
                "description": "ID of the speaker profile to rename.",
            },
            "new_name": {
                "type": "string",
                "description": "New display name.",
            },
        },
        ["speaker_id", "new_name"],
        _rename_args,
    ),
    "cancel": (
        {},
        [],
        _empty_args,
    ),
    "create_tool": (
        {
            "name": {
                "type": "string",
                "description": "Tool name (lowercase, no spaces, letters/numbers/underscores only)",
            },
            "description": {
                "type": "string",
                "description": "What the tool does",
            },
            "arguments": {
                "type": "object",
                "description": "JSON schema for tool arguments as a dict mapping arg names to their type/description",
            },
            "implementation_type": {
                "type": "string",
                "enum": ["bash", "fetch", "python"],
                "description": "How the tool is executed: bash (shell command), fetch (HTTP GET), or python (eval expression)",
            },
            "implementation": {
                "type": "string",
                "description": "The command, URL, or Python code. Use {arg} placeholders for bash/fetch, args dict for python.",
            },
        },
        ["name", "description", "arguments", "implementation_type", "implementation"],
        _json_args,
    ),
    "update_tool": (
        {
            "name": {
                "type": "string",
                "description": "Tool name to update",
            },
            "description": {
                "type": "string",
                "description": "New description (optional)",
            },
            "arguments": {
                "type": "object",
                "description": "New arguments schema (optional)",
            },
            "implementation_type": {
                "type": "string",
                "enum": ["bash", "fetch", "python"],
                "description": "New implementation type (optional)",
            },
            "implementation": {
                "type": "string",
                "description": "New implementation string (optional)",
            },
        },
        ["name"],
        _json_args,
    ),
    "delete_tool": (
        {
            "name": {
                "type": "string",
                "description": "Tool name to delete",
            },
        },
        ["name"],
        _name_args,
    ),
    "list_tools": (
        {},
        [],
        _empty_args,
    ),
    # Archive & Batch Tools
    "create_archive": (
        {
            "archive_path": {
                "type": "string",
                "description": "Path where archive will be created",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of source files/directories to include",
            },
            "format": {
                "type": "string",
                "enum": ["tar.gz", "zip", "tar.bz2"],
                "description": "Archive format (default: tar.gz)",
            },
            "compression": {
                "type": "string",
                "enum": ["auto", "gzip", "bzip2", "none"],
                "description": "Compression method (default: auto)",
            },
        },
        ["archive_path", "sources"],
        _json_args,
    ),
    "extract_archive": (
        {
            "archive_path": {
                "type": "string",
                "description": "Path to archive file",
            },
            "destination": {
                "type": "string",
                "description": "Directory to extract to",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite existing files (default: false)",
            },
            "preserve_path": {
                "type": "boolean",
                "description": "Preserve archive directory structure (default: true)",
            },
        },
        ["archive_path", "destination"],
        _json_args,
    ),
    "batch_operation": (
        {
            "operation": {
                "type": "string",
                "enum": ["delete", "copy_to", "move_to", "list_info", "archive"],
                "description": "Operation to perform",
            },
            "pattern": {
                "type": "string",
                "description": "File pattern to match (e.g., '*.py', 'temp_')",
            },
            "source": {
                "type": "string",
                "description": "Source directory or file (default: workspace)",
            },
            "include_subdirs": {
                "type": "boolean",
                "description": "Include subdirectories (default: false)",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Show what would be done without actually doing it (default: false)",
            },
        },
        ["operation", "pattern"],
        _json_args,
    ),
    # Profile Management Tools
    "add_favorite": (
        {
            "path": {
                "type": "string",
                "description": "Directory path to add to favorites",
            },
            "label": {
                "type": "string",
                "description": "Optional label for the favorite location",
            },
        },
        ["path"],
        _path_args,
    ),
    "list_favorites": (
        {
            "limit": {
                "type": "integer",
                "description": "Maximum number of favorites to return (default: 10)",
            },
        },
        [],
        _empty_args,
    ),
    "set_view_preference": (
        {
            "key": {
                "type": "string",
                "description": "Preference key (show_hidden, detail_level, sort_by, sort_order)",
            },
            "value": {
                "type": "string",
                "description": "Value to set (string, boolean, or integer)",
            },
        },
        ["key", "value"],
        _json_args,
    ),
    "show_profile": (
        {
            "section": {
                "type": "string",
                "enum": ["all", "preferences", "favorites", "history"],
                "description": "Profile section to show (default: all)",
            },
        },
        [],
        _empty_args,
    ),
    # Agent Skills meta-tools
    "list_skills": (
        {},
        [],
        _empty_args,
    ),
    "run_skill": (
        {
            "name": {
                "type": "string",
                "description": "Name of the skill to run (e.g., 'pdf-processing')",
            },
            "context": {
                "type": "object",
                "description": "Skill-specific context dict. Contents depend on the skill. Pass {} if the skill needs no parameters. Call list_skills first to learn what each skill expects.",
                "properties": {},
                "additionalProperties": True,
            },
        },
        ["name", "context"],
        _json_args,
    ),
    "set_provider": (
        {
            "provider": {
                "type": "string",
                "enum": ["openrouter", "zai"],
                "description": "LLM provider to switch to (openrouter or zai)",
            },
        },
        ["provider"],
        _provider_args,
    ),
    "set_mode": (
        {
            "mode": {
                "type": "string",
                "enum": [
                    "ambient",
                    "focus",
                    "silent",
                    "assistant",
                    "meeting",
                ],
                "description": "Behavior mode to switch to",
            },
        },
        ["mode"],
        _mode_args,
    ),
    "show_display": (
        {
            "content": {
                "type": "string",
                "description": "The full block to render on the dashboard (may be long, multi-line).",
            },
            "format": {
                "type": "string",
                "enum": ["text", "code", "ascii", "table", "markdown"],
                "description": "How to render content. 'code' = syntax-highlighted, 'ascii' = monospace as-is, 'markdown'/'table' = rich, 'text' = plain.",
            },
            "title": {
                "type": "string",
                "description": "Optional short heading for the panel.",
            },
        },
        ["content", "format"],
        _display_args,
    ),
    # Capability discovery (US-007)
    "discover_capability": (
        {
            "intent": {
                "type": "string",
                "description": "The user's intent / transcript describing what they want. Example: 'weather in kyiv'.",
            },
            "prefer_remote": {
                "type": "boolean",
                "description": "Set true when the user explicitly asks about marketplace/online skills (e.g. 'what skills exist online', 'search the marketplace'). Skips local index and queries skillsmp.com directly. Default false.",
            },
        },
        ["intent"],
        _json_args,
    ),
    "install_skill_tool": (
        {
            "slug": {
                "type": "string",
                "description": "Skill slug returned from discover_capability.",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user said yes via voice. Never set true otherwise.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing skill with the same slug. Default false.",
            },
        },
        ["slug", "user_confirmed"],
        _json_args,
    ),
    "stop_daemon": (
        {
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent. Stops the daemon — the conversation will end.",
            },
        },
        ["user_confirmed"],
        _json_args,
    ),
    "restart_daemon": (
        {
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent. Restarts the daemon — there will be a brief silence as the new process comes up.",
            },
        },
        ["user_confirmed"],
        _json_args,
    ),
    "create_skill": (
        {
            "name": {
                "type": "string",
                "description": (
                    "Skill slug — lowercase letters, digits, hyphens; 1–64 chars; "
                    "must start and end with [a-z0-9]. Example: 'audio-debug'."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "One-line summary the user (and the LLM) will see when "
                    "listing skills. Max 200 chars."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Markdown body of the skill — the procedure the LLM will "
                    "follow when run_skill is invoked. Use existing tools "
                    "(bash, read, write, web_search). Max 1 MB."
                ),
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user said yes via voice. Never set true otherwise.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing skill with the same name. Default false.",
            },
        },
        ["name", "description", "body", "user_confirmed"],
        _json_args,
    ),
    "install_mcp_server_tool": (
        {
            "slug": {
                "type": "string",
                "description": "MCP server slug returned from discover_capability.",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user said yes via voice. Never set true otherwise.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing MCP server with the same slug. Default false.",
            },
        },
        ["slug", "user_confirmed"],
        _json_args,
    ),
    "register_mcp_server": (
        {
            "slug": {
                "type": "string",
                "description": "Lowercase slug, [a-z0-9-]+. Becomes the key in .mcp.json and the prefix for mcp__<slug>__* tools.",
            },
            "description": {
                "type": "string",
                "description": "One-line description of what this MCP server does (max 200 chars).",
            },
            "command": {
                "type": "string",
                "description": "Launch command — typically 'npx', 'uvx', 'python', 'node', etc.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Argument list passed to the command (e.g., ['-y', '@foo/server']).",
            },
            "env": {
                "type": "object",
                "description": "Optional env vars (str->str). Use sparingly and never embed secrets verbatim.",
            },
            "source_url": {
                "type": "string",
                "description": "Optional URL to the server's repo or docs (must be on github.com or skillsmp.com).",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user explicitly confirmed the read-back of slug, command, args, and env.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing MCP server with the same slug. Default false.",
            },
        },
        ["slug", "description", "command", "args", "user_confirmed"],
        _json_args,
    ),
    "revoke_capability": (
        {
            "slug": {
                "type": "string",
                "description": "Slug of the skill or MCP server to uninstall.",
            },
        },
        ["slug"],
        _json_args,
    ),
    "list_capabilities": (
        {
            "category": {
                "type": "string",
                "description": "Optional category filter (e.g., 'skill', 'mcp').",
            },
        },
        [],
        _json_args,
    ),
    # Browser bridge tools (Phase 1)
    "read_browser_page": (
        {
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID to read. Omit to use the active tab.",
            },
        },
        [],
        _json_args,
    ),
    "list_browser_tabs": (
        {},
        [],
        _empty_args,
    ),
    "click_in_browser": (
        {
            "selector": {
                "type": "string",
                "description": "CSS selector identifying the element to click.",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        ["selector"],
        _json_args,
    ),
    "fill_in_browser": (
        {
            "selector": {
                "type": "string",
                "description": "CSS selector identifying the input element to fill.",
            },
            "value": {
                "type": "string",
                "description": "Text value to enter into the field.",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        ["selector", "value"],
        _json_args,
    ),
    "navigate_browser": (
        {
            "url": {
                "type": "string",
                "description": "URL to navigate the tab to (must start with http:// or https://).",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        ["url"],
        _json_args,
    ),
    "extract_in_browser": (
        {
            "selector": {
                "type": "string",
                "description": "CSS selector to match DOM elements.",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        ["selector"],
        _json_args,
    ),
    "open_browser_tab": (
        {
            "url": {
                "type": "string",
                "description": "URL to open in a new tab (must start with http:// or https://).",
            },
        },
        ["url"],
        _json_args,
    ),
    "activate_browser_tab": (
        {
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID to bring to the foreground. Omit to use the currently-active tab (no-op in that case).",
            },
        },
        [],
        _json_args,
    ),
}


def build_tools_schema():
    """Return a ``ToolsSchema`` covering every enabled tool in the registry.

    Pipecat is imported lazily so admin CLI paths that import this
    module without portaudio installed still load.
    """
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    enabled = get_enabled_tools()
    schemas: list = []
    for name, tool in TOOLS.items():
        if not tool.enabled or name not in enabled:
            continue
        spec = _TOOL_SPECS.get(name)
        if spec is None:
            logger.warning(
                "llm_tools: tool %r enabled in registry but no schema "
                "defined; skipping",
                name,
            )
            continue
        properties, required, _ = spec
        schemas.append(
            FunctionSchema(
                name=name,
                description=tool.description,
                properties=properties,
                required=required,
            )
        )

    # Add dynamic tools schemas
    from src.agent.tools.registry import _DYNAMIC_TOOLS
    for name in _DYNAMIC_TOOLS:
        if name not in enabled:
            continue
        dynamic_schema = get_dynamic_tool_schema(name)
        if dynamic_schema:
            schemas.append(
                FunctionSchema(
                    name=name,
                    description=get_tool(name).description,
                    properties=dynamic_schema[0],
                    required=dynamic_schema[1],
                )
            )

    return ToolsSchema(standard_tools=schemas)


_intent_id_seq = itertools.count(start=1)


def _make_handler(
    tool_name: str,
    serializer: ArgsSerializer,
    settings: "Settings | None",
    conversation_manager: Any = None,
    session_state: Any = None,
) -> Callable[[Any], Awaitable[None]]:
    """Build a Pipecat ``FunctionCallParams`` handler for one tool.

    When ``conversation_manager`` is supplied, the handler also threads
    each invocation through ``record_action_pending`` / ``record_action_result``
    / ``record_action_cancelled`` / ``record_action_error`` so the
    conversation memory's recent-actions block stays populated for the
    next-turn LLM context (CCS-02 / CCS-04 grounding rules).

    When ``session_state`` is supplied, the current mode's tool-deny
    policy is enforced at execution time (the model still sees the tool
    in its schema, but a denied call is short-circuited with a spoken
    refusal). This is also the seam the audio-bleed safety gate will use.
    """

    async def handler(params: Any) -> None:
        args_str = serializer(dict(params.arguments or {}))
        intent_id = next(_intent_id_seq)
        from src.agent.modes import mode_gate_refusal

        refusal = mode_gate_refusal(session_state, tool_name)
        if refusal is not None:
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_pending(
                        intent_id, tool_name, args_str
                    )
                    conversation_manager.record_action_error(
                        intent_id, refusal["error"]
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "llm_tools: mode_gate action-log failed "
                        "(non-fatal)"
                    )
            await params.result_callback(refusal)
            return
        if conversation_manager is not None:
            try:
                conversation_manager.record_action_pending(
                    intent_id, tool_name, args_str
                )
            except Exception:  # noqa: BLE001 — never break the tool call
                logger.exception(
                    "llm_tools: record_action_pending failed (non-fatal)"
                )
        try:
            result = await execute_direct(tool_name, args_str, settings)
        except asyncio.CancelledError:
            # Bubble up so Pipecat's runner can mark the call cancelled.
            # ``execute_direct`` already kills the bash process group on
            # the way out (CCS-05b lives inside ``_execute_bash``).
            logger.info(
                "llm_tools: handler %r cancelled; relying on direct_tools "
                "kill paths for child cleanup",
                tool_name,
            )
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_cancelled(
                        intent_id, tool=tool_name, args=args_str
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "llm_tools: record_action_cancelled failed (non-fatal)"
                    )
            raise
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.exception("llm_tools: handler %r raised", tool_name)
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_error(intent_id, repr(exc))
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "llm_tools: record_action_error failed (non-fatal)"
                    )
            await params.result_callback(
                {
                    "success": False,
                    "output": "",
                    "error": f"{tool_name} handler error: {exc!s}",
                }
            )
            return
        if conversation_manager is not None:
            try:
                summary = (
                    str(result.get("summary") or result.get("output") or "")
                    if isinstance(result, dict)
                    else str(result)
                )
                items = (
                    result.get("items")
                    if isinstance(result, dict) and isinstance(result.get("items"), list)
                    else None
                )
                conversation_manager.record_action_result(
                    intent_id, summary, items=items
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "llm_tools: record_action_result failed (non-fatal)"
                )
        await params.result_callback(result)

    handler.__name__ = f"_handle_{tool_name}"
    return handler


def register_all_tools(
    llm: Any,
    *,
    settings: "Settings | None" = None,
    conversation_manager: Any = None,
    session_state: Any = None,
) -> list[str]:
    """Register one ``FunctionCallParams`` handler per enabled tool.

    Returns the list of tool names actually registered, primarily for
    test / log assertions. When ``conversation_manager`` is supplied,
    every tool invocation is bracketed with action-log records so the
    next-turn LLM context's ``recent_actions`` block stays populated
    (CCS-02 / CCS-04 grounding rules).
    """
    enabled = get_enabled_tools()
    registered: list[str] = []
    for name, tool in TOOLS.items():
        if not tool.enabled or name not in enabled:
            continue
        spec = _TOOL_SPECS.get(name)
        if spec is None:
            logger.warning(
                "llm_tools: register_all_tools: tool %r has no schema; "
                "skipping",
                name,
            )
            continue
        _, _, serializer = spec
        handler = _make_handler(
            name, serializer, settings, conversation_manager, session_state
        )
        # cancel is an InterruptionFrame-driven concept after PH2-05; the
        # handler itself never executes for real cancels but we register
        # it so the LLM has a parsable function name in its tool surface.
        cancel_on_interruption = name != "cancel"
        llm.register_function(
            name,
            handler,
            cancel_on_interruption=cancel_on_interruption,
        )
        registered.append(name)
    return registered


# ---------------------------------------------------------------------------
# Dynamic tool schema registration — for runtime tool creation
# ---------------------------------------------------------------------------

# Store for dynamic tool schemas
_DYNAMIC_TOOL_SCHEMAS: dict[str, tuple[dict, str, str]] = {}


def register_dynamic_tool_schema(
    name: str,
    schema: dict,
    impl_type: str,
    impl: str,
) -> None:
    """Register a dynamic tool's schema for immediate use."""
    _DYNAMIC_TOOL_SCHEMAS[name] = (schema, impl_type, impl)


def unregister_dynamic_tool_schema(name: str) -> bool:
    """Unregister a dynamic tool's schema. Returns True if existed."""
    return _DYNAMIC_TOOL_SCHEMAS.pop(name, None) is not None


def get_dynamic_tool_schema(name: str) -> tuple[dict, str, str] | None:
    """Get a dynamic tool's schema."""
    return _DYNAMIC_TOOL_SCHEMAS.get(name)


def register_dynamic_tool_handler(
    llm: Any,
    name: str,
    impl_type: str,
    impl: str,
    settings: "Settings | None" = None,
    conversation_manager: Any = None,
) -> None:
    """Create and register a handler for a dynamically created tool."""
    from src.agent.tools.dynamic import execute_bash_tool, execute_fetch_tool, execute_python_tool

    async def handler(params: Any) -> None:
        args = dict(params.arguments or {})

        # Execute based on type
        if impl_type == "bash":
            result = await execute_bash_tool(impl, args, settings)
        elif impl_type == "fetch":
            result = await execute_fetch_tool(impl, args, settings)
        elif impl_type == "python":
            result = await execute_python_tool(impl, args, settings)
        else:
            result = {"success": False, "error": f"Unknown impl_type: {impl_type}"}

        await params.result_callback(result)

    llm.register_function(name, handler, cancel_on_interruption=True)


__all__ = [
    "build_tools_schema",
    "register_all_tools",
    "register_dynamic_tool_schema",
    "unregister_dynamic_tool_schema",
    "register_dynamic_tool_handler",
]
