"""Auto-generate tool schemas and registration from definitions.

Centralises tool metadata in a single ``TOOLS`` list and derives:

* :func:`build_tools_schema` — ``ToolsSchema`` for the LLM surface
* :func:`register_all_tools` — Pipecat ``register_function`` wiring

Handler dispatch maps each tool's *handler type* to an async execution
function in :mod:`.direct`. Lazy imports avoid circular dependencies
with the pipeline layer.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("heare.tools.system")


@dataclass(frozen=True)
class ToolDef:
    """Metadata for one agent tool — the single source of truth."""

    name: str
    description: str
    handler: str
    schema_fields: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    enabled: bool = True

    def __post_init__(self):
        if not self.required and self.schema_fields:
            object.__setattr__(self, "required", list(self.schema_fields.keys()))


ArgsSerializer = Callable[[dict[str, Any]], str]


def _bash_serializer(args: dict[str, Any]) -> str:
    return str(args.get("command", "")).strip()


def _path_serializer(args: dict[str, Any]) -> str:
    return str(args.get("path", "")).strip()


def _write_serializer(args: dict[str, Any]) -> str:
    path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    return f"{path}: {content}"


def _url_serializer(args: dict[str, Any]) -> str:
    return str(args.get("url", "")).strip()


def _query_serializer(args: dict[str, Any]) -> str:
    return str(args.get("query", "")).strip()


def _name_serializer(args: dict[str, Any]) -> str:
    return str(args.get("name", "")).strip()


def _json_serializer(args: dict[str, Any]) -> str:
    return json.dumps(args)


def _show_text_serializer(args: dict[str, Any]) -> str:
    args["format"] = "text"
    return json.dumps(args)


def _show_canvas_serializer(args: dict[str, Any]) -> str:
    args["format"] = "html"
    return json.dumps(args)


def _empty_serializer(_args: dict[str, Any]) -> str:
    return ""


def _provider_serializer(args: dict[str, Any]) -> str:
    return str(args.get("provider", "")).strip()


def _mode_serializer(args: dict[str, Any]) -> str:
    return str(args.get("mode", "")).strip()


TOOLS: list[ToolDef] = [
    ToolDef(
        name="bash",
        description="Execute shell commands in the workspace directory.",
        handler="bash",
        schema_fields={
            "command": {"type": "string", "description": "The shell command to execute."}
        },
    ),
    ToolDef(
        name="read",
        description="Read file contents from the workspace.",
        handler="file_read",
        schema_fields={
            "path": {"type": "string", "description": "Absolute path to the file to read."}
        },
    ),
    ToolDef(
        name="write",
        description="Write content to a file (format: 'filepath: content').",
        handler="file_write",
        schema_fields={
            "path": {"type": "string", "description": "Absolute path to the file to create or overwrite."},
            "content": {"type": "string", "description": "The full file contents to write."},
        },
    ),
    ToolDef(
        name="web_fetch",
        description="Fetch and return content from a URL.",
        handler="web_fetch",
        schema_fields={
            "url": {"type": "string", "description": "URL to fetch and return the content of."}
        },
    ),
    ToolDef(
        name="web_search",
        description="Search the web (uses Serper.dev if key available, else DuckDuckGo).",
        handler="web_search",
        schema_fields={
            "query": {"type": "string", "description": "Search query."}
        },
    ),
    ToolDef(
        name="cancel",
        description="Cancel the in-flight action and drain pending intents.",
        handler="cancel",
        schema_fields={},
    ),
    ToolDef(
        name="create_tool",
        description="Create a new tool dynamically. Provide: name, description, arguments (JSON schema), implementation type (bash/fetch), and implementation string.",
        handler="tool_create",
        schema_fields={
            "name": {"type": "string", "description": "Tool name (lowercase, no spaces, letters/numbers/underscores only)."},
            "description": {"type": "string", "description": "What the tool does."},
            "arguments": {"type": "object", "description": "JSON schema for tool arguments as a dict mapping arg names to their type/description."},
            "implementation_type": {"type": "string", "enum": ["bash", "fetch"], "description": "How the tool is executed: bash (shell command) or fetch (HTTP GET)."},
            "implementation": {"type": "string", "description": "The command or URL. Use {arg} placeholders for bash/fetch."},
        },
    ),
    ToolDef(
        name="update_tool",
        description="Update an existing dynamic tool. Provide the tool name and fields to update.",
        handler="tool_update",
        schema_fields={
            "name": {"type": "string", "description": "Tool name to update."},
            "description": {"type": "string", "description": "New description (optional)."},
            "arguments": {"type": "object", "description": "New arguments schema (optional)."},
            "implementation_type": {"type": "string", "enum": ["bash", "fetch"], "description": "New implementation type (optional)."},
            "implementation": {"type": "string", "description": "New implementation string (optional)."},
        },
        required=["name"],
    ),
    ToolDef(
        name="delete_tool",
        description="Delete a dynamic tool by name. Cannot delete built-in tools.",
        handler="tool_delete",
        schema_fields={
            "name": {"type": "string", "description": "Tool name to delete."},
        },
    ),
    ToolDef(
        name="list_tools",
        description="List all available tools, including dynamically created ones.",
        handler="tool_list",
        schema_fields={},
    ),
    ToolDef(
        name="create_archive",
        description="Create tar or zip archive from files/directories with compression options.",
        handler="archive_create",
        schema_fields={
            "archive_path": {"type": "string", "description": "Path where archive will be created."},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "List of source files/directories to include."},
            "format": {"type": "string", "enum": ["tar.gz", "zip", "tar.bz2"], "description": "Archive format (default: tar.gz)."},
            "compression": {"type": "string", "enum": ["auto", "gzip", "bzip2", "none"], "description": "Compression method (default: auto)."},
        },
        required=["archive_path", "sources"],
    ),
    ToolDef(
        name="extract_archive",
        description="Extract tar or zip archive to a directory with overwrite options.",
        handler="archive_extract",
        schema_fields={
            "archive_path": {"type": "string", "description": "Path to archive file."},
            "destination": {"type": "string", "description": "Directory to extract to."},
            "overwrite": {"type": "boolean", "description": "Overwrite existing files (default: false)."},
            "preserve_path": {"type": "boolean", "description": "Preserve archive directory structure (default: true)."},
        },
        required=["archive_path", "destination"],
    ),
    ToolDef(
        name="batch_operation",
        description="Perform operations on multiple files matching a pattern (delete, copy, move, list, archive).",
        handler="batch_op",
        schema_fields={
            "operation": {"type": "string", "enum": ["delete", "copy_to", "move_to", "list_info", "archive"], "description": "Operation to perform."},
            "pattern": {"type": "string", "description": "File pattern to match (e.g., '*.py', 'temp_')."},
            "source": {"type": "string", "description": "Source directory or file (default: workspace)."},
            "include_subdirs": {"type": "boolean", "description": "Include subdirectories (default: false)."},
            "dry_run": {"type": "boolean", "description": "Show what would be done without actually doing it (default: false)."},
        },
        required=["operation", "pattern"],
    ),
    ToolDef(
        name="add_favorite",
        description="Add a directory to favorites list.",
        handler="profile_favorite_add",
        schema_fields={
            "path": {"type": "string", "description": "Directory path to add to favorites."},
            "label": {"type": "string", "description": "Optional label for the favorite location."},
        },
        required=["path"],
    ),
    ToolDef(
        name="list_favorites",
        description="List favorite locations with access counts.",
        handler="profile_favorite_list",
        schema_fields={
            "limit": {"type": "integer", "description": "Maximum number of favorites to return (default: 10)."},
        },
    ),
    ToolDef(
        name="set_view_preference",
        description="Set display preferences (show_hidden, detail_level, sort_by, sort_order).",
        handler="profile_view_pref",
        schema_fields={
            "key": {"type": "string", "description": "Preference key (show_hidden, detail_level, sort_by, sort_order)."},
            "value": {"type": "string", "description": "Value to set (string, boolean, or integer)."},
        },
        required=["key", "value"],
    ),
    ToolDef(
        name="show_profile",
        description="Show current user profile settings and preferences.",
        handler="profile_show",
        schema_fields={
            "section": {"type": "string", "enum": ["all", "preferences", "favorites", "history"], "description": "Profile section to show (default: all)."},
        },
    ),
    ToolDef(
        name="list_skills",
        description="List available Agent Skills. Returns skill names and one-line descriptions. Call this to discover what portable skills exist.",
        handler="skill_list",
        schema_fields={},
    ),
    ToolDef(
        name="run_skill",
        description="Execute an Agent Skill by name. Skills can orchestrate multiple heare tools internally. Provide the skill name and context dict with required parameters.",
        handler="skill_run",
        schema_fields={
            "name": {"type": "string", "description": "Name of the skill to run (e.g., 'pdf-processing')."},
            "context": {"type": "object", "description": "Skill-specific context dict. Pass {} if the skill needs no parameters.", "properties": {}, "additionalProperties": True},
        },
        required=["name", "context"],
    ),
    ToolDef(
        name="set_provider",
        description="Switch the active LLM provider (deepseek, zai, or opencode). Change takes effect on the next user utterance.",
        handler="provider_set",
        schema_fields={
            "provider": {"type": "string", "description": "LLM provider to switch to (deepseek, zai, or opencode)."},
        },
    ),
    ToolDef(
        name="set_mode",
        description="Switch the agent's behavior mode: ambient (default conversational), focus (terse/fast), silent (speak only when addressed), assistant (proactive, full tools), meeting (passive note-taker). Takes effect immediately.",
        handler="mode_set",
        schema_fields={
            "mode": {"type": "string", "enum": ["ambient", "focus", "silent", "assistant", "meeting"], "description": "Behavior mode to switch to."},
        },
    ),
    ToolDef(
        name="show_text",
        description="Show text on the display panel. Use when voice is unavailable or when content is better read than heard.",
        handler="display",
        schema_fields={
            "content": {"type": "string", "description": "Text to display."},
            "title": {"type": "string", "description": "Optional heading."},
        },
        required=["content"],
    ),
    ToolDef(
        name="show_canvas",
        description="Render HTML/JS in the canvas panel. Use for charts, diagrams, visual demos, UI components.",
        handler="display",
        schema_fields={
            "content": {"type": "string", "description": "HTML/JS to render in canvas."},
            "title": {"type": "string", "description": "Optional heading."},
        },
        required=["content"],
    ),
    ToolDef(
        name="discover_capability",
        description="Search for an installable skill or MCP server matching the user's intent. Use when the user asks for something you don't have an existing tool for.",
        handler="capability_discover",
        schema_fields={
            "intent": {"type": "string", "description": "The user's intent / transcript describing what they want."},
            "prefer_remote": {"type": "boolean", "description": "Set true to skip local index and query marketplace directly. Default false."},
        },
        required=["intent"],
    ),
    ToolDef(
        name="install_skill_tool",
        description="Install a skill from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        handler="capability_install_skill",
        schema_fields={
            "slug": {"type": "string", "description": "Skill slug returned from discover_capability."},
            "user_confirmed": {"type": "boolean", "description": "Set true ONLY after the user said yes via voice."},
            "replace": {"type": "boolean", "description": "Overwrite an existing skill with the same slug. Default false."},
        },
        required=["slug", "user_confirmed"],
    ),
    ToolDef(
        name="create_skill",
        description="Author a new local skill from the conversation. Requires user_confirmed=true after explicit voice consent.",
        handler="capability_create_skill",
        schema_fields={
            "name": {"type": "string", "description": "Skill slug — lowercase letters, digits, hyphens; 1–64 chars."},
            "description": {"type": "string", "description": "One-line summary (max 200 chars)."},
            "body": {"type": "string", "description": "Markdown body of the skill — the procedure the LLM will follow."},
            "user_confirmed": {"type": "boolean", "description": "Set true ONLY after the user said yes via voice."},
            "replace": {"type": "boolean", "description": "Overwrite an existing skill with the same name. Default false."},
        },
        required=["name", "description", "body", "user_confirmed"],
    ),
    ToolDef(
        name="install_mcp_server_tool",
        description="Install an MCP server from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        handler="capability_install_mcp",
        schema_fields={
            "slug": {"type": "string", "description": "MCP server slug returned from discover_capability."},
            "user_confirmed": {"type": "boolean", "description": "Set true ONLY after the user said yes via voice."},
            "replace": {"type": "boolean", "description": "Overwrite an existing MCP server with the same slug. Default false."},
        },
        required=["slug", "user_confirmed"],
    ),
    ToolDef(
        name="register_mcp_server",
        description="Register an MCP server directly from user-supplied launch info. Use ONLY when discover_capability has no matching entry.",
        handler="capability_register_mcp",
        schema_fields={
            "slug": {"type": "string", "description": "Lowercase slug, [a-z0-9-]+."},
            "description": {"type": "string", "description": "One-line description (max 200 chars)."},
            "command": {"type": "string", "description": "Launch command (npx, uvx, python, node, etc.)."},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Argument list passed to the command."},
            "env": {"type": "object", "description": "Optional env vars (str->str)."},
            "source_url": {"type": "string", "description": "Optional URL to the server's repo or docs."},
            "user_confirmed": {"type": "boolean", "description": "Set true ONLY after explicit voice consent."},
            "replace": {"type": "boolean", "description": "Overwrite an existing MCP server with the same slug. Default false."},
        },
        required=["slug", "description", "command", "args", "user_confirmed"],
    ),
    ToolDef(
        name="revoke_capability",
        description="Uninstall a previously installed skill or MCP server by slug.",
        handler="capability_revoke",
        schema_fields={
            "slug": {"type": "string", "description": "Slug of the skill or MCP server to uninstall."},
        },
        required=["slug"],
    ),
    ToolDef(
        name="list_capabilities",
        description="List everything the agent can call, grouped into built_in, skills, and mcps buckets.",
        handler="capability_list",
        schema_fields={
            "category": {"type": "string", "description": "Optional category filter (e.g., 'skill', 'mcp')."},
        },
    ),
    ToolDef(
        name="stop_daemon",
        description="Gracefully stop the running daemon. Requires user_confirmed=true after explicit voice consent.",
        handler="daemon_stop",
        schema_fields={
            "user_confirmed": {"type": "boolean", "description": "Set true ONLY after explicit voice consent."},
        },
        required=["user_confirmed"],
    ),
    ToolDef(
        name="restart_daemon",
        description="Restart the running daemon. This is the ONLY safe way to restart from inside the daemon. Requires user_confirmed=true.",
        handler="daemon_restart",
        schema_fields={
            "user_confirmed": {"type": "boolean", "description": "Set true ONLY after explicit voice consent."},
        },
        required=["user_confirmed"],
    ),
    ToolDef(
        name="read_browser_page",
        description="Read the URL, title, and text content of a browser tab via the Heare Bridge extension.",
        handler="browser_read",
        schema_fields={
            "tab_id": {"type": "integer", "description": "Chrome tab ID to read. Omit to use the active tab."},
        },
    ),
    ToolDef(
        name="list_browser_tabs",
        description="List all open tabs in the connected Chrome browser with their id, url, title, and active state.",
        handler="browser_list_tabs",
        schema_fields={},
    ),
    ToolDef(
        name="click_in_browser",
        description="Click an element in a browser tab identified by a CSS selector.",
        handler="browser_click",
        schema_fields={
            "selector": {"type": "string", "description": "CSS selector identifying the element to click."},
            "tab_id": {"type": "integer", "description": "Chrome tab ID. Omit to use the active tab."},
        },
        required=["selector"],
    ),
    ToolDef(
        name="fill_in_browser",
        description="Fill a form field in a browser tab identified by a CSS selector.",
        handler="browser_fill",
        schema_fields={
            "selector": {"type": "string", "description": "CSS selector identifying the input element to fill."},
            "value": {"type": "string", "description": "Text value to enter into the field."},
            "tab_id": {"type": "integer", "description": "Chrome tab ID. Omit to use the active tab."},
        },
        required=["selector", "value"],
    ),
    ToolDef(
        name="navigate_browser",
        description="Navigate a browser tab to a URL and wait for the page to load.",
        handler="browser_navigate",
        schema_fields={
            "url": {"type": "string", "description": "URL to navigate the tab to (must start with http:// or https://)."},
            "tab_id": {"type": "integer", "description": "Chrome tab ID. Omit to use the active tab."},
        },
        required=["url"],
    ),
    ToolDef(
        name="extract_in_browser",
        description="Extract matching DOM elements from a browser tab by CSS selector.",
        handler="browser_extract",
        schema_fields={
            "selector": {"type": "string", "description": "CSS selector to match DOM elements."},
            "tab_id": {"type": "integer", "description": "Chrome tab ID. Omit to use the active tab."},
        },
        required=["selector"],
    ),
    ToolDef(
        name="open_browser_tab",
        description="Open a new tab in the connected Chrome browser and navigate it to the given URL.",
        handler="browser_open_tab",
        schema_fields={
            "url": {"type": "string", "description": "URL to open in a new tab (must start with http:// or https://)."},
        },
        required=["url"],
    ),
    ToolDef(
        name="activate_browser_tab",
        description="Bring an existing browser tab to the foreground without changing its URL or reloading it.",
        handler="browser_activate_tab",
        schema_fields={
            "tab_id": {"type": "integer", "description": "Chrome tab ID to bring to the foreground."},
        },
    ),
]


_SERIALIZERS: dict[str, ArgsSerializer] = {
    "bash": _bash_serializer,
    "read": _path_serializer,
    "write": _write_serializer,
    "web_fetch": _url_serializer,
    "web_search": _query_serializer,
    "cancel": _empty_serializer,
    "create_tool": _json_serializer,
    "update_tool": _json_serializer,
    "delete_tool": _name_serializer,
    "list_tools": _empty_serializer,
    "create_archive": _json_serializer,
    "extract_archive": _json_serializer,
    "batch_operation": _json_serializer,
    "add_favorite": _path_serializer,
    "list_favorites": _empty_serializer,
    "set_view_preference": _json_serializer,
    "show_profile": _empty_serializer,
    "list_skills": _empty_serializer,
    "run_skill": _json_serializer,
    "set_provider": _provider_serializer,
    "set_mode": _mode_serializer,
    "show_text": _show_text_serializer,
    "show_canvas": _show_canvas_serializer,
    "discover_capability": _json_serializer,
    "install_skill_tool": _json_serializer,
    "create_skill": _json_serializer,
    "install_mcp_server_tool": _json_serializer,
    "register_mcp_server": _json_serializer,
    "revoke_capability": _json_serializer,
    "list_capabilities": _json_serializer,
    "stop_daemon": _json_serializer,
    "restart_daemon": _json_serializer,
    "read_browser_page": _json_serializer,
    "list_browser_tabs": _empty_serializer,
    "click_in_browser": _json_serializer,
    "fill_in_browser": _json_serializer,
    "navigate_browser": _json_serializer,
    "extract_in_browser": _json_serializer,
    "open_browser_tab": _json_serializer,
    "activate_browser_tab": _json_serializer,
}


def _handler_for(tool: ToolDef):
    """Return the handler function for a tool's handler type from :mod:`.direct`."""
    from . import direct

    handler_map = {
        "bash": direct._execute_bash,
        "file_read": direct._execute_read,
        "file_write": direct._execute_write,
        "web_fetch": direct._execute_web_fetch,
        "web_search": direct._execute_web_search,
        "cancel": direct._execute_bash,
        "tool_create": direct._execute_create_tool,
        "tool_update": direct._execute_update_tool,
        "tool_delete": direct._execute_delete_tool,
        "tool_list": direct._execute_list_tools,
        "archive_create": direct._execute_create_archive,
        "archive_extract": direct._execute_extract_archive,
        "batch_op": direct._execute_batch_operation,
        "profile_favorite_add": direct._execute_add_favorite,
        "profile_favorite_list": direct._execute_list_favorites,
        "profile_view_pref": direct._execute_set_view_preference,
        "profile_show": direct._execute_show_profile,
        "skill_list": direct._execute_list_skills,
        "skill_run": direct._execute_run_skill,
        "provider_set": direct._execute_set_provider,
        "mode_set": direct._execute_set_mode,
        "display": direct._execute_show_display,
        "capability_discover": direct._execute_discover_capability,
        "capability_install_skill": direct._execute_install_skill_tool,
        "capability_create_skill": direct._execute_create_skill,
        "capability_install_mcp": direct._execute_install_mcp_server_tool,
        "capability_register_mcp": direct._execute_register_mcp_server,
        "capability_revoke": direct._execute_revoke_capability,
        "capability_list": direct._execute_list_capabilities,
        "daemon_stop": direct._execute_stop_daemon,
        "daemon_restart": direct._execute_restart_daemon,
        "browser_read": direct._execute_read_browser_page,
        "browser_list_tabs": direct._execute_list_browser_tabs,
        "browser_click": direct._execute_click_in_browser,
        "browser_fill": direct._execute_fill_in_browser,
        "browser_navigate": direct._execute_navigate_browser,
        "browser_extract": direct._execute_extract_in_browser,
        "browser_open_tab": direct._execute_open_browser_tab,
        "browser_activate_tab": direct._execute_activate_browser_tab,
    }

    func = handler_map.get(tool.handler)
    if func is None:
        logger.warning("No handler for type %r, tool %s", tool.handler, tool.name)
    return func


def build_tools_schema() -> Any:
    """Build the ``ToolsSchema`` for LLM context."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    schemas: list[Any] = []
    for t in TOOLS:
        if not t.enabled:
            continue
        schemas.append(
            FunctionSchema(
                name=t.name,
                description=t.description,
                properties=t.schema_fields,
                required=t.required,
            )
        )

    return ToolsSchema(standard_tools=schemas)


_intent_id_seq = itertools.count(start=1)


def _make_handler(
    tool: ToolDef,
    direct_func: Any,
    serializer: ArgsSerializer | None,
    settings: Any = None,
    conversation_manager: Any = None,
    session_state: Any = None,
) -> Callable[[Any], Any]:
    """Build a Pipecat ``FunctionCallParams`` handler for one tool."""
    ser = serializer or _json_serializer

    async def handler(params: Any) -> None:
        args_str = ser(dict(params.arguments or {}))
        intent_id = next(_intent_id_seq)

        from src.agent.modes import mode_gate_refusal

        refusal = mode_gate_refusal(session_state, tool.name)
        if refusal is not None:
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_pending(intent_id, tool.name, args_str)
                    conversation_manager.record_action_error(intent_id, refusal["error"])
                except Exception:
                    logger.exception("system: mode_gate action-log failed (non-fatal)")
            await params.result_callback(refusal)
            return

        if conversation_manager is not None:
            try:
                conversation_manager.record_action_pending(intent_id, tool.name, args_str)
            except Exception:
                logger.exception("system: record_action_pending failed (non-fatal)")

        try:
            result = await direct_func(args_str, settings=settings)
        except asyncio.CancelledError:
            logger.info("system: handler %r cancelled", tool.name)
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_cancelled(intent_id, tool=tool.name, args=args_str)
                except Exception:
                    logger.exception("system: record_action_cancelled failed (non-fatal)")
            raise
        except Exception as exc:
            logger.exception("system: handler %r raised", tool.name)
            if conversation_manager is not None:
                try:
                    conversation_manager.record_action_error(intent_id, repr(exc))
                except Exception:
                    logger.exception("system: record_action_error failed (non-fatal)")
            await params.result_callback(
                {"success": False, "output": "", "error": f"{tool.name} handler error: {exc!s}"}
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
                conversation_manager.record_action_result(intent_id, summary, items=items)
            except Exception:
                logger.exception("system: record_action_result failed (non-fatal)")

        await params.result_callback(result)

    handler.__name__ = f"_handle_{tool.name}"
    return handler


def register_all_tools(
    llm: Any,
    *,
    settings: Any = None,
    conversation_manager: Any = None,
    session_state: Any = None,
) -> list[str]:
    """Register one ``FunctionCallParams`` handler per enabled tool.

    Returns the list of tool names actually registered.
    """
    registered: list[str] = []
    for t in TOOLS:
        if not t.enabled:
            continue

        direct_func = _handler_for(t)
        if direct_func is None:
            logger.warning("system: tool %r has no handler; skipping registration", t.name)
            continue

        serializer = _SERIALIZERS.get(t.name)
        handler = _make_handler(
            t, direct_func, serializer,
            settings=settings, conversation_manager=conversation_manager, session_state=session_state,
        )

        cancel_on_interruption = t.name != "cancel"
        llm.register_function(t.name, handler, cancel_on_interruption=cancel_on_interruption)
        registered.append(t.name)

    return registered


_DYNAMIC_TOOL_SCHEMAS: dict[str, tuple[dict[str, Any], str, str]] = {}


def register_dynamic_tool_schema(name: str, schema: dict[str, Any], impl_type: str, impl: str) -> None:
    """Register a dynamic tool's schema for immediate use."""
    _DYNAMIC_TOOL_SCHEMAS[name] = (schema, impl_type, impl)


def unregister_dynamic_tool_schema(name: str) -> bool:
    """Unregister a dynamic tool's schema."""
    return _DYNAMIC_TOOL_SCHEMAS.pop(name, None) is not None


def get_dynamic_tool_schema(name: str) -> tuple[dict[str, Any], str, str] | None:
    """Get a dynamic tool's schema."""
    return _DYNAMIC_TOOL_SCHEMAS.get(name)


def register_dynamic_tool_handler(
    llm: Any, name: str, impl_type: str, impl: str,
    settings: Any = None, conversation_manager: Any = None,
) -> None:
    """Create and register a handler for a dynamically created tool."""
    from src.agent.tools.dynamic import execute_bash_tool, execute_fetch_tool

    async def handler(params: Any) -> None:
        args = dict(params.arguments or {})

        if impl_type == "bash":
            result = await execute_bash_tool(impl, args, settings)
        elif impl_type == "fetch":
            result = await execute_fetch_tool(impl, args, settings)
        else:
            result = {"success": False, "error": f"Unknown impl_type: {impl_type}"}

        await params.result_callback(result)

    llm.register_function(name, handler, cancel_on_interruption=True)


__all__ = [
    "ToolDef",
    "TOOLS",
    "build_tools_schema",
    "register_all_tools",
    "register_dynamic_tool_schema",
    "unregister_dynamic_tool_schema",
    "get_dynamic_tool_schema",
    "register_dynamic_tool_handler",
]
