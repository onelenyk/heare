"""Auto-generate tool schemas and registration from definitions.

Centralises tool metadata in a single ``TOOLS`` list and derives:


Handler dispatch maps each tool's *handler type* to an async execution
function in :mod:`.direct`. Lazy imports avoid circular dependencies
with the pipeline layer.
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings

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


# The verbs the conversational agent keeps when the voice/hands split is
# on. Everything else moves behind `delegate`, which returns instantly.
#
# The split is temporal, not intellectual: same model, same key. One call
# must answer within a second and therefore chooses among three schemas;
# the other has no deadline and sees all sixty-three. Measured on this
# machine, a turn that calls a tool inline takes 3822 ms to first audio
# against 1351 ms without one — and whether anything is said before the
# work begins is otherwise left to the model's whim.
# `forget` is the fourth, and it belongs here rather than behind
# `delegate` for the same reason the other three do: it has to land while
# the person is still in the room. "Forget the last hour" answered a
# minute later, after a worker has been spun up, is an apology rather
# than an action — and the thing it erases is the one thing someone
# might be waiting to see gone.
VOICE_TOOLS = frozenset({"delegate", "remember", "recall", "forget"})




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


TOOLS: list[ToolDef] = [
    ToolDef(
        name="bash",
        description="Execute shell commands in the workspace directory.",
        handler="bash",
        schema_fields={
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            }
        },
    ),
    ToolDef(
        name="read",
        description="Read file contents from the workspace.",
        handler="file_read",
        schema_fields={
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            }
        },
    ),
    ToolDef(
        name="write",
        description="Write content to a file (format: 'filepath: content').",
        handler="file_write",
        schema_fields={
            "path": {
                "type": "string",
                "description": "Absolute path to the file to create or overwrite.",
            },
            "content": {
                "type": "string",
                "description": "The full file contents to write.",
            },
        },
    ),
    ToolDef(
        name="web_fetch",
        description="Fetch and return content from a URL.",
        handler="web_fetch",
        schema_fields={
            "url": {
                "type": "string",
                "description": "URL to fetch and return the content of.",
            }
        },
    ),
    ToolDef(
        name="web_search",
        description="Search the web (uses Serper.dev if key available, else DuckDuckGo).",
        handler="web_search",
        schema_fields={"query": {"type": "string", "description": "Search query."}},
    ),
    ToolDef(
        name="delegate",
        description=(
            "Hand a task to your worker: anything needing files, the shell, "
            "the web, settings, the browser, or more than a moment's thought. "
            "Returns at once — say in one short sentence that you are on it, "
            "then stop. The answer arrives later as its own message."
        ),
        handler="delegate",
        schema_fields={
            "task": {
                "type": "string",
                "description": (
                    "What to do, in full. The worker cannot see this "
                    "conversation, so name everything it needs."
                ),
            }
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
            "name": {
                "type": "string",
                "description": "Tool name (lowercase, no spaces, letters/numbers/underscores only).",
            },
            "description": {"type": "string", "description": "What the tool does."},
            "arguments": {
                "type": "object",
                "description": "JSON schema for tool arguments as a dict mapping arg names to their type/description.",
            },
            "implementation_type": {
                "type": "string",
                "enum": ["bash", "fetch"],
                "description": "How the tool is executed: bash (shell command) or fetch (HTTP GET).",
            },
            "implementation": {
                "type": "string",
                "description": "The command or URL. Use {arg} placeholders for bash/fetch.",
            },
        },
    ),
    ToolDef(
        name="update_tool",
        description="Update an existing dynamic tool. Provide the tool name and fields to update.",
        handler="tool_update",
        schema_fields={
            "name": {"type": "string", "description": "Tool name to update."},
            "description": {
                "type": "string",
                "description": "New description (optional).",
            },
            "arguments": {
                "type": "object",
                "description": "New arguments schema (optional).",
            },
            "implementation_type": {
                "type": "string",
                "enum": ["bash", "fetch"],
                "description": "New implementation type (optional).",
            },
            "implementation": {
                "type": "string",
                "description": "New implementation string (optional).",
            },
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
            "archive_path": {
                "type": "string",
                "description": "Path where archive will be created.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of source files/directories to include.",
            },
            "format": {
                "type": "string",
                "enum": ["tar.gz", "zip", "tar.bz2"],
                "description": "Archive format (default: tar.gz).",
            },
            "compression": {
                "type": "string",
                "enum": ["auto", "gzip", "bzip2", "none"],
                "description": "Compression method (default: auto).",
            },
        },
        required=["archive_path", "sources"],
    ),
    ToolDef(
        name="extract_archive",
        description="Extract tar or zip archive to a directory with overwrite options.",
        handler="archive_extract",
        schema_fields={
            "archive_path": {"type": "string", "description": "Path to archive file."},
            "destination": {
                "type": "string",
                "description": "Directory to extract to.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite existing files (default: false).",
            },
            "preserve_path": {
                "type": "boolean",
                "description": "Preserve archive directory structure (default: true).",
            },
        },
        required=["archive_path", "destination"],
    ),
    ToolDef(
        name="batch_operation",
        description="Perform operations on multiple files matching a pattern (delete, copy, move, list, archive).",
        handler="batch_op",
        schema_fields={
            "operation": {
                "type": "string",
                "enum": ["delete", "copy_to", "move_to", "list_info", "archive"],
                "description": "Operation to perform.",
            },
            "pattern": {
                "type": "string",
                "description": "File pattern to match (e.g., '*.py', 'temp_').",
            },
            "source": {
                "type": "string",
                "description": "Source directory or file (default: workspace).",
            },
            "include_subdirs": {
                "type": "boolean",
                "description": "Include subdirectories (default: false).",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Show what would be done without actually doing it (default: false).",
            },
        },
        required=["operation", "pattern"],
    ),
    ToolDef(
        name="add_favorite",
        description="Add a directory to favorites list.",
        handler="profile_favorite_add",
        schema_fields={
            "path": {
                "type": "string",
                "description": "Directory path to add to favorites.",
            },
            "label": {
                "type": "string",
                "description": "Optional label for the favorite location.",
            },
        },
        required=["path"],
    ),
    ToolDef(
        name="list_favorites",
        description="List favorite locations with access counts.",
        handler="profile_favorite_list",
        schema_fields={
            "limit": {
                "type": "integer",
                "description": "Maximum number of favorites to return (default: 10).",
            },
        },
    ),
    ToolDef(
        name="set_view_preference",
        description="Set display preferences (show_hidden, detail_level, sort_by, sort_order).",
        handler="profile_view_pref",
        schema_fields={
            "key": {
                "type": "string",
                "description": "Preference key (show_hidden, detail_level, sort_by, sort_order).",
            },
            "value": {
                "type": "string",
                "description": "Value to set (string, boolean, or integer).",
            },
        },
        required=["key", "value"],
    ),
    ToolDef(
        name="show_profile",
        description="Show current user profile settings and preferences.",
        handler="profile_show",
        schema_fields={
            "section": {
                "type": "string",
                "enum": ["all", "preferences", "favorites", "history"],
                "description": "Profile section to show (default: all).",
            },
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
            "name": {
                "type": "string",
                "description": "Name of the skill to run (e.g., 'pdf-processing').",
            },
            "context": {
                "type": "object",
                "description": "Skill-specific context dict. Pass {} if the skill needs no parameters.",
                "properties": {},
                "additionalProperties": True,
            },
        },
        required=["name", "context"],
    ),
    ToolDef(
        name="set_provider",
        description="Switch the active LLM provider (deepseek, zai, or opencode). Change takes effect on the next user utterance.",
        handler="provider_set",
        schema_fields={
            "provider": {
                "type": "string",
                "description": "LLM provider to switch to (deepseek, zai, or opencode).",
            },
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
            "content": {
                "type": "string",
                "description": "HTML/JS to render in canvas.",
            },
            "title": {"type": "string", "description": "Optional heading."},
        },
        required=["content"],
    ),
    ToolDef(
        name="read_display",
        description="Read what is currently on the screen panel. The panel's contents are not in your context — call this when you need to see or reference what the user is looking at.",
        handler="display_read",
        schema_fields={},
        required=[],
    ),
    ToolDef(
        name="discover_capability",
        description="Search for an installable skill or MCP server matching the user's intent. Use when the user asks for something you don't have an existing tool for.",
        handler="capability_discover",
        schema_fields={
            "intent": {
                "type": "string",
                "description": "The user's intent / transcript describing what they want.",
            },
            "prefer_remote": {
                "type": "boolean",
                "description": "Set true to skip local index and query marketplace directly. Default false.",
            },
        },
        required=["intent"],
    ),
    ToolDef(
        name="install_skill_tool",
        description="Install a skill from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        handler="capability_install_skill",
        schema_fields={
            "slug": {
                "type": "string",
                "description": "Skill slug returned from discover_capability.",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user said yes via voice.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing skill with the same slug. Default false.",
            },
        },
        required=["slug", "user_confirmed"],
    ),
    ToolDef(
        name="create_skill",
        description="Author a new local skill from the conversation. Requires user_confirmed=true after explicit voice consent.",
        handler="capability_create_skill",
        schema_fields={
            "name": {
                "type": "string",
                "description": "Skill slug — lowercase letters, digits, hyphens; 1–64 chars.",
            },
            "description": {
                "type": "string",
                "description": "One-line summary (max 200 chars).",
            },
            "body": {
                "type": "string",
                "description": "Markdown body of the skill — the procedure the LLM will follow.",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user said yes via voice.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing skill with the same name. Default false.",
            },
        },
        required=["name", "description", "body", "user_confirmed"],
    ),
    ToolDef(
        name="install_mcp_server_tool",
        description="Install an MCP server from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        handler="capability_install_mcp",
        schema_fields={
            "slug": {
                "type": "string",
                "description": "MCP server slug returned from discover_capability.",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after the user said yes via voice.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing MCP server with the same slug. Default false.",
            },
        },
        required=["slug", "user_confirmed"],
    ),
    ToolDef(
        name="register_mcp_server",
        description="Register an MCP server directly from user-supplied launch info. Use ONLY when discover_capability has no matching entry.",
        handler="capability_register_mcp",
        schema_fields={
            "slug": {"type": "string", "description": "Lowercase slug, [a-z0-9-]+."},
            "description": {
                "type": "string",
                "description": "One-line description (max 200 chars).",
            },
            "command": {
                "type": "string",
                "description": "Launch command (npx, uvx, python, node, etc.).",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Argument list passed to the command.",
            },
            "env": {"type": "object", "description": "Optional env vars (str->str)."},
            "source_url": {
                "type": "string",
                "description": "Optional URL to the server's repo or docs.",
            },
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing MCP server with the same slug. Default false.",
            },
        },
        required=["slug", "description", "command", "args", "user_confirmed"],
    ),
    ToolDef(
        name="revoke_capability",
        description="Uninstall a previously installed skill or MCP server by slug.",
        handler="capability_revoke",
        schema_fields={
            "slug": {
                "type": "string",
                "description": "Slug of the skill or MCP server to uninstall.",
            },
        },
        required=["slug"],
    ),
    ToolDef(
        name="list_capabilities",
        description="List everything the agent can call, grouped into built_in, skills, and mcps buckets.",
        handler="capability_list",
        schema_fields={
            "category": {
                "type": "string",
                "description": "Optional category filter (e.g., 'skill', 'mcp').",
            },
        },
    ),
    ToolDef(
        name="stop_daemon",
        description="Gracefully stop the running daemon. Requires user_confirmed=true after explicit voice consent.",
        handler="daemon_stop",
        schema_fields={
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent.",
            },
        },
        required=["user_confirmed"],
    ),
    ToolDef(
        name="restart_daemon",
        description="Restart the running daemon. This is the ONLY safe way to restart from inside the daemon. Requires user_confirmed=true.",
        handler="daemon_restart",
        schema_fields={
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent.",
            },
        },
        required=["user_confirmed"],
    ),
    ToolDef(
        name="read_browser_page",
        description="Read the URL, title, and text content of a browser tab via the Heare Bridge extension.",
        handler="browser_read",
        schema_fields={
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID to read. Omit to use the active tab.",
            },
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
            "selector": {
                "type": "string",
                "description": "CSS selector identifying the element to click.",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        required=["selector"],
    ),
    ToolDef(
        name="fill_in_browser",
        description="Fill a form field in a browser tab identified by a CSS selector.",
        handler="browser_fill",
        schema_fields={
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
        required=["selector", "value"],
    ),
    ToolDef(
        name="navigate_browser",
        description="Navigate a browser tab to a URL and wait for the page to load.",
        handler="browser_navigate",
        schema_fields={
            "url": {
                "type": "string",
                "description": "URL to navigate the tab to (must start with http:// or https://).",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        required=["url"],
    ),
    ToolDef(
        name="extract_in_browser",
        description="Extract matching DOM elements from a browser tab by CSS selector.",
        handler="browser_extract",
        schema_fields={
            "selector": {
                "type": "string",
                "description": "CSS selector to match DOM elements.",
            },
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID. Omit to use the active tab.",
            },
        },
        required=["selector"],
    ),
    ToolDef(
        name="open_browser_tab",
        description="Open a new tab in the connected Chrome browser and navigate it to the given URL.",
        handler="browser_open_tab",
        schema_fields={
            "url": {
                "type": "string",
                "description": "URL to open in a new tab (must start with http:// or https://).",
            },
        },
        required=["url"],
    ),
    ToolDef(
        name="activate_browser_tab",
        description="Bring an existing browser tab to the foreground without changing its URL or reloading it.",
        handler="browser_activate_tab",
        schema_fields={
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID to bring to the foreground.",
            },
        },
    ),
    ToolDef(
        # OFF: there is nothing behind it. handler="batch_op" points at
        # _execute_batch_operation, which copies and deletes files by
        # pattern — the label was copied, the sequencing was never
        # written. Offered to the model, it is a tool that can be chosen
        # and cannot run; the worker already sequences its own steps.
        enabled=False,
        name="workflow",
        description="Execute a multi-step action sequence. Provide a list of tools to call in order. Each step waits for the previous one to complete.",
        handler="batch_op",
        schema_fields={
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of actions with tool name and args.",
            },
        },
        required=["steps"],
    ),
    ToolDef(
        name="mute_bot",
        description="Mute or unmute the bot's voice output. When muted, the bot hears but does not speak.",
        handler="mute_bot",
        schema_fields={
            "muted": {
                "type": "boolean",
                "description": "True to mute, False to unmute.",
            },
        },
        required=["muted"],
    ),
    ToolDef(
        name="mute_mic",
        description="Mute or unmute the microphone input. When muted, the bot cannot hear anything.",
        handler="mute_mic",
        schema_fields={
            "muted": {
                "type": "boolean",
                "description": "True to mute, False to unmute.",
            },
        },
        required=["muted"],
    ),
    ToolDef(
        name="vad_sensitivity",
        description="Adjust VAD (Voice Activity Detection) sensitivity. Higher values (closer to 1.0) make the bot less likely to interrupt, requiring louder or clearer speech to trigger. Lower values (closer to 0.0) make it more sensitive to any sound.",
        handler="vad_sensitivity",
        schema_fields={
            "level": {
                "type": "number",
                "description": "Sensitivity level from 0.0 (very sensitive) to 1.0 (least sensitive). Default 0.5.",
            },
        },
        required=["level"],
    ),
    ToolDef(
        name="mic_gain",
        description="Adjust the microphone input gain. Higher values amplify quiet speech, lower values reduce loud input. Unity gain is 1.0.",
        handler="mic_gain",
        schema_fields={
            "gain": {
                "type": "number",
                "description": "Gain multiplier from 0.0 (mute) to 5.0 (5x amplification). Default 1.0.",
            },
        },
        required=["gain"],
    ),
    ToolDef(
        name="volume",
        description="Adjust the speaker output volume. Changes how loud the bot speaks through the speakers.",
        handler="volume",
        schema_fields={
            "level": {
                "type": "number",
                "description": "Volume level from 0.0 (silent) to 5.0 (5x amplification). Default 1.0.",
            },
        },
        required=["level"],
    ),
    ToolDef(
        name="audio_input",
        description="Switch the audio input device (microphone). Provide the device name or substring to match.",
        handler="audio_device",
        schema_fields={
            "name": {
                "type": "string",
                "description": "Device name or substring to match (e.g., 'AirPods Pro').",
            },
        },
        required=["name"],
    ),
    ToolDef(
        name="audio_output",
        description="Switch the audio output device (speakers). Provide the device name or substring to match.",
        handler="audio_device",
        schema_fields={
            "name": {
                "type": "string",
                "description": "Device name or substring to match (e.g., 'AirPods Pro').",
            },
        },
        required=["name"],
    ),
    ToolDef(
        name="run_agent",
        description="Delegate a complex multi-step task to an OpenCode sub-agent. Use for analysis, code changes, research, or any task requiring multiple steps. Returns the answer, cost, and a session_id you can pass to continue the conversation.",
        handler="subagent_run",
        schema_fields={
            "prompt": {
                "type": "string",
                "description": "The task description or question for the sub-agent. Be specific — include file paths, context, and what you need.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the sub-agent (default: current workspace).",
            },
            "model": {
                "type": "string",
                "description": "Model to use (default: opencode's configured model).",
            },
            "session_id": {
                "type": "string",
                "description": "Continue a previous sub-agent session by its ID. Omit to start a new session.",
            },
        },
        required=["prompt"],
    ),
    ToolDef(
        name="agent_start",
        description="Start a background sub-agent via OpenCode server. Returns session_id immediately. The agent runs asynchronously.",
        handler="agent_start",
        schema_fields={
            "prompt": {
                "type": "string",
                "description": "Task description for the sub-agent.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (default: current workspace).",
            },
        },
        required=["prompt"],
    ),
    ToolDef(
        name="agent_status",
        description="Check progress of a background sub-agent. Shows current_step, tool_calls, cost, elapsed time.",
        handler="agent_status",
        schema_fields={
            "session_id": {
                "type": "string",
                "description": "Session ID from agent_start.",
            },
        },
        required=["session_id"],
    ),
    ToolDef(
        name="agent_result",
        description="Get the full output from a sub-agent. Returns partial output if still running, complete if done.",
        handler="agent_result",
        schema_fields={
            "session_id": {
                "type": "string",
                "description": "Session ID from agent_start.",
            },
        },
        required=["session_id"],
    ),
    ToolDef(
        name="agent_message",
        description="Continue a conversation with an existing sub-agent session. Only on done/error/cancelled agents.",
        handler="agent_message",
        schema_fields={
            "session_id": {
                "type": "string",
                "description": "Session ID from agent_start.",
            },
            "prompt": {
                "type": "string",
                "description": "Follow-up task or correction.",
            },
        },
        required=["session_id", "prompt"],
    ),
    ToolDef(
        name="agent_cancel",
        description="Cancel a running sub-agent and stop its server. Preserves partial output.",
        handler="agent_cancel",
        schema_fields={
            "session_id": {
                "type": "string",
                "description": "Session ID from agent_start.",
            },
        },
        required=["session_id"],
    ),
    ToolDef(
        name="agent_list",
        description="List all managed sub-agents with their status, progress, and cost.",
        handler="agent_list",
        schema_fields={},
    ),
    ToolDef(
        name="agent_approve",
        description="Approve a pending permission request from a sub-agent. Use when context shows waiting_for_input.",
        handler="agent_approve",
        schema_fields={
            "session_id": {
                "type": "string",
                "description": "Session ID from agent_start.",
            },
        },
        required=["session_id"],
    ),
    ToolDef(
        name="agent_deny",
        description="Deny a pending permission request. Sends corrective message if reason provided.",
        handler="agent_deny",
        schema_fields={
            "session_id": {
                "type": "string",
                "description": "Session ID from agent_start.",
            },
            "reason": {
                "type": "string",
                "description": "Why denied — becomes corrective message to the agent.",
            },
        },
        required=["session_id"],
    ),
    ToolDef(
        name="remember",
        description="Store a fact in persistent memory.",
        handler="remember",
        schema_fields={
            "type": {
                "type": "string",
                "enum": ["fact", "preference", "decision", "event"],
                "description": "Type of memory to store.",
            },
            "content": {
                "type": "string",
                "description": "What to remember. A short sentence.",
            },
        },
        required=["type", "content"],
    ),
    ToolDef(
        name="recall",
        description="Search your persistent memory.",
        handler="recall",
        schema_fields={
            "query": {
                "type": "string",
                "description": "What to search for in your memories.",
            },
        },
        required=["query"],
    ),
    ToolDef(
        name="forget",
        description="Remove a memory by ID.",
        handler="forget",
        schema_fields={
            "memory_id": {
                "type": "string",
                "description": "The ID of the memory to forget (from recall results).",
            },
        },
        required=["memory_id"],
    ),
    ToolDef(
        name="memory_status",
        description="Show memory statistics.",
        handler="memory_status",
        schema_fields={},
    ),
    ToolDef(
        name="sidetone",
        description="Увімкнути або вимкнути моніторинг мікрофону — "
        "ви будете чути себе в динаміках так, як вас чує асистент. "
        "Корисно для перевірки якості звуку та уникнення відлуння.",
        handler="sidetone",
        schema_fields={
            "enabled": {
                "type": "boolean",
                "description": "True щоб увімкнути, False щоб вимкнути.",
            },
        },
        required=["enabled"],
    ),
]


_SERIALIZERS: dict[str, ArgsSerializer] = {
    "bash": _bash_serializer,
    "read": _path_serializer,
    "write": _write_serializer,
    "web_fetch": _url_serializer,
    "web_search": _query_serializer,
    "cancel": _empty_serializer,
    "delegate": lambda args: str(args.get("task", "")).strip(),
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
    "workflow": _json_serializer,
    "mute_bot": _json_serializer,
    "mute_mic": _json_serializer,
    "audio_input": _name_serializer,
    "audio_output": _name_serializer,
    "run_agent": _json_serializer,
    "agent_start": _json_serializer,
    "agent_status": _json_serializer,
    "agent_result": _json_serializer,
    "agent_message": _json_serializer,
    "agent_cancel": _json_serializer,
    "agent_list": _empty_serializer,
    "agent_approve": _json_serializer,
    "agent_deny": _json_serializer,
    "remember": _json_serializer,
    "recall": _json_serializer,
    "forget": _json_serializer,
    "memory_status": _json_serializer,
    "vad_sensitivity": _json_serializer,
    "mic_gain": _json_serializer,
    "volume": _json_serializer,
    "sidetone": _json_serializer,
}








_intent_id_seq = itertools.count(start=1)


# ── Tool execution deadlines ──────────────────────────────────────────
#
# Pipecat arms a timeout task for EVERY function call and defaults it to
# 10s (``LLMService.function_call_timeout_secs``). On expiry it delivers
# ``result=None``; the assistant aggregator writes that to the context as
# a bare "COMPLETED" and — because the result is falsy — never re-runs the
# LLM. The turn dies silently: no reply, no error, nothing the model can
# act on, and the real result is discarded when it finally arrives.
#
# Every tool slower than 10s hit this: bash (no timeout of its own at
# all), web_search (30s search + 30s page fetch), run_agent (120s), any
# install. It is why slow work sounded like the agent had ignored you.
#
# So we own the deadline instead. ``_make_handler`` enforces the value
# below with ``asyncio.wait_for`` and hands the model a real, actionable
# error; registration gives pipecat a strictly later deadline so our
# message always wins the race.

DEFAULT_TOOL_TIMEOUT_SECS = 30.0

# Headroom between our deadline and pipecat's fallback. Only reached if
# our own wait_for somehow fails to fire.
_PIPECAT_TIMEOUT_MARGIN_SECS = 15.0

# Tools whose work is legitimately slower than the default. Keyed by tool
# name, like ``_SERIALIZERS``. Each value sits ABOVE that tool's own
# internal timeout, so the tool gets to report its specific failure
# before this blunter one fires.
_TOOL_TIMEOUTS: dict[str, float] = {
    "bash": 60.0,
    "web_fetch": 45.0,  # httpx timeout is 30s
    "web_search": 90.0,  # 30s search + up to 30s top-page fetch
    "run_agent": 150.0,  # opencode_default_timeout is 120s
    "run_skill": 60.0,
    "install_skill_tool": 120.0,
    "install_mcp_server_tool": 120.0,
    "register_mcp_server": 60.0,
    "discover_capability": 60.0,
    "create_archive": 60.0,
    "extract_archive": 60.0,
    "read_browser_page": 45.0,
    "extract_in_browser": 45.0,
    "navigate_browser": 45.0,
}


def tool_timeout_secs(name: str) -> float:
    """Our execution deadline for tool `name`, in seconds."""
    return _TOOL_TIMEOUTS.get(name, DEFAULT_TOOL_TIMEOUT_SECS)






_DYNAMIC_TOOL_SCHEMAS: dict[str, tuple[dict[str, Any], str, str]] = {}


def register_dynamic_tool_schema(
    name: str, schema: dict[str, Any], impl_type: str, impl: str
) -> None:
    """Register a dynamic tool's schema for immediate use."""
    _DYNAMIC_TOOL_SCHEMAS[name] = (schema, impl_type, impl)


def unregister_dynamic_tool_schema(name: str) -> bool:
    """Unregister a dynamic tool's schema."""
    return _DYNAMIC_TOOL_SCHEMAS.pop(name, None) is not None


def get_dynamic_tool_schema(name: str) -> tuple[dict[str, Any], str, str] | None:
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


def get_tool(name: str) -> "ToolDef | None":
    for t in TOOLS:
        if t.name == name:
            return t
    return None


def get_tool_names() -> list[str]:
    return [t.name for t in TOOLS]


def get_handler_types() -> list[str]:
    return sorted({t.handler for t in TOOLS})


def get_tools_by_handler(handler: str) -> list["ToolDef"]:
    return [t for t in TOOLS if t.handler == handler]


__all__ = [
    "ToolDef",
    "TOOLS",
    "register_dynamic_tool_schema",
    "unregister_dynamic_tool_schema",
    "get_dynamic_tool_schema",
    "register_dynamic_tool_handler",
    "get_tool",
    "get_tool_names",
    "get_handler_types",
    "get_tools_by_handler",
]
