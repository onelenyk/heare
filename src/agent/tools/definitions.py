"""Data-driven tool definitions — single source of truth for all agent tools.

Ports the 41 built-in tools from the original trio of registry.py, schemas.py,
and direct.py into a declarative ``ToolDef`` list. Each entry carries:

* ``name`` — lowercase identifier used in intents/prompts.
* ``description`` — human-readable purpose.
* ``handler`` — backend dispatch key (categorises the tool by execution pattern).
* ``params`` — handler-specific static configuration (e.g. ``format`` for display).
* ``schema_fields`` — LLM-facing JSON-schema properties dict (one key per argument).

Handler type mapping (every tool maps to exactly ONE of these)::

    "display"       → show_text, show_canvas (writes to displays table)
    "bash"          → execute shell command
    "file_read"     → read file
    "file_write"    → write/edit file
    "web_search"    → search web
    "web_fetch"     → fetch URL
    "browser"       → browser automation (list, navigate, click, fill, extract, open, activate)
    "daemon"        → daemon control (set_mode, set_provider, stop, restart)
    "skill_list"    → list_skills, list_capabilities, list_favorites, list_tools
    "skill_run"     → run_skill
    "skill_create"  → create_skill, create_tool
    "skill_install" → install_skill_tool, install_mcp_server_tool, register_mcp_server
    "skill_delete"  → delete_tool
    "skill_update"  → update_tool
    "skill_discover"→ discover_capability, revoke_capability
    "misc"          → create_archive, extract_archive, batch_operation,
                       add_favorite, set_view_preference, show_profile, cancel
    "mute_bot"      → mute_bot
    "mute_mic"      → mute_mic
    "audio_device"  → audio_input, audio_output

Tools with no LLM arguments (like ``list_skills``, ``stop_daemon``) have an
empty ``schema_fields`` dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDef:
    """Immutable definition of a single agent tool capability."""

    name: str
    description: str
    handler: str  # backend dispatch key — see module docstring
    params: dict = field(default_factory=dict)
    schema_fields: dict = field(default_factory=dict)


# ============================================================================
# COMPLETE TOOL LIST — 45 built-in tools
# ============================================================================

TOOLS: list[ToolDef] = [

    # ── OUTPUT / DISPLAY TOOLS ───────────────────────────────────────────────

    ToolDef(
        name="show_text",
        description="Show text on the display panel. Use when voice is unavailable (muted/silent) or when content is better read than heard: facts, lists, references, explanations. Speak a short pointer ('showing on screen') then call this.",
        handler="display",
        params={"format": "text"},
        schema_fields={
            "content": {
                "type": "string",
                "description": "Text to display.",
            },
            "title": {
                "type": "string",
                "description": "Optional heading.",
            },
        },
    ),
    ToolDef(
        name="show_canvas",
        description="Render HTML/JS in the canvas panel. Use for charts, diagrams, visual demos, UI components. Speak a short pointer ('rendering chart') then call this.",
        handler="display",
        params={"format": "html"},
        schema_fields={
            "content": {
                "type": "string",
                "description": "HTML/JS to render in canvas.",
            },
            "title": {
                "type": "string",
                "description": "Optional heading.",
            },
        },
    ),

    # ── SHELL TOOLS ──────────────────────────────────────────────────────────

    ToolDef(
        name="bash",
        description="Execute shell commands in the workspace directory",
        handler="bash",
        schema_fields={
            "command": {
                "type": "string",
                "description": "The shell command to execute in the workspace.",
            },
        },
    ),

    # ── FILE TOOLS ───────────────────────────────────────────────────────────

    ToolDef(
        name="read",
        description="Read file contents from the workspace",
        handler="file_read",
        schema_fields={
            "path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
        },
    ),
    ToolDef(
        name="write",
        description="Write content to a file (format: 'filepath: content')",
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

    # ── WEB TOOLS ────────────────────────────────────────────────────────────

    ToolDef(
        name="web_fetch",
        description="Fetch and return content from a URL",
        handler="web_fetch",
        schema_fields={
            "url": {
                "type": "string",
                "description": "URL to fetch and return the content of.",
            },
        },
    ),
    ToolDef(
        name="web_search",
        description="Search the web (uses Serper.dev if key available, else DuckDuckGo)",
        handler="web_search",
        schema_fields={
            "query": {
                "type": "string",
                "description": "Search query.",
            },
        },
    ),

    # ── DAEMON CONTROL TOOLS ─────────────────────────────────────────────────

    ToolDef(
        name="cancel",
        description="Cancel the in-flight action and drain pending intents",
        handler="daemon",
    ),
    ToolDef(
        name="set_mode",
        description="Switch the agent's behavior mode: ambient (default conversational), focus (terse/fast), silent (speak only when addressed), assistant (proactive, full tools), meeting (passive note-taker, no side-effect tools). Takes effect immediately.",
        handler="daemon",
        schema_fields={
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
    ),
    ToolDef(
        name="set_provider",
        description="Switch the active LLM provider (deepseek, zai, or opencode). Change takes effect on the next user utterance.",
        handler="daemon",
        schema_fields={
            "provider": {
                "type": "string",
                "description": "LLM provider to switch to (deepseek, zai, or opencode)",
            },
        },
    ),
    ToolDef(
        name="stop_daemon",
        description="Gracefully stop the running daemon. Use ONLY when the user explicitly asks to stop / shut down / quit / kill the bot. Never call this to 'free resources' or as part of any other task. Requires user_confirmed=true after explicit voice consent.",
        handler="daemon",
        schema_fields={
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent. Stops the daemon — the conversation will end.",
            },
        },
    ),
    ToolDef(
        name="restart_daemon",
        description="Restart the running daemon. Use when the user asks to restart / reload after config or code changes. This is the ONLY safe way to restart from inside the daemon — never run `make restart` or `hearectl restart` via bash, those terminate the agent without bringing it back. Requires user_confirmed=true.",
        handler="daemon",
        schema_fields={
            "user_confirmed": {
                "type": "boolean",
                "description": "Set true ONLY after explicit voice consent. Restarts the daemon — there will be a brief silence as the new process comes up.",
            },
        },
    ),

    # ── SKILL / CAPABILITY LISTING TOOLS ─────────────────────────────────────

    ToolDef(
        name="list_skills",
        description="List available Agent Skills. Returns skill names and one-line descriptions. Call this to discover what portable skills exist.",
        handler="skill_list",
    ),
    ToolDef(
        name="list_capabilities",
        description="List everything the agent can call, grouped into three buckets: built_in (code-backed tools), skills (markdown procedures), mcps (external MCP servers). Optional 'category' filter: built_in | skills | mcps.",
        handler="skill_list",
        schema_fields={
            "category": {
                "type": "string",
                "description": "Optional category filter (e.g., 'skill', 'mcp').",
            },
        },
    ),
    ToolDef(
        name="list_tools",
        description="List all available tools, including dynamically created ones.",
        handler="skill_list",
    ),
    ToolDef(
        name="list_favorites",
        description="List favorite locations with access counts",
        handler="skill_list",
        schema_fields={
            "limit": {
                "type": "integer",
                "description": "Maximum number of favorites to return (default: 10)",
            },
        },
    ),

    # ── SKILL EXECUTION ──────────────────────────────────────────────────────

    ToolDef(
        name="run_skill",
        description="Execute an Agent Skill by name. Skills can orchestrate multiple heare tools internally. Provide the skill name and context dict with required parameters.",
        handler="skill_run",
        schema_fields={
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
    ),

    # ── SKILL / TOOL CREATION ────────────────────────────────────────────────

    ToolDef(
        name="create_skill",
        description="Author a new local skill from the conversation. Use when the user asks to remember a procedure or workflow as a reusable skill. The skill body is markdown instructions the LLM will read when run_skill is later invoked. Requires user_confirmed=true after explicit voice consent.",
        handler="skill_create",
        schema_fields={
            "name": {
                "type": "string",
                "description": "Skill slug — lowercase letters, digits, hyphens; 1–64 chars; must start and end with [a-z0-9]. Example: 'audio-debug'.",
            },
            "description": {
                "type": "string",
                "description": "One-line summary the user (and the LLM) will see when listing skills. Max 200 chars.",
            },
            "body": {
                "type": "string",
                "description": "Markdown body of the skill — the procedure the LLM will follow when run_skill is invoked. Use existing tools (bash, read, write, web_search). Max 1 MB.",
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
    ),
    ToolDef(
        name="create_tool",
        description="Create a new tool dynamically. Provide: name, description, arguments (JSON schema), implementation type (bash/fetch/python), and implementation string.",
        handler="skill_create",
        schema_fields={
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
    ),

    # ── SKILL / MCP INSTALLATION ─────────────────────────────────────────────

    ToolDef(
        name="install_skill_tool",
        description="Install a skill from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        handler="skill_install",
        schema_fields={
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
    ),
    ToolDef(
        name="install_mcp_server_tool",
        description="Install an MCP server from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        handler="skill_install",
        schema_fields={
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
    ),
    ToolDef(
        name="register_mcp_server",
        description="Register an MCP server directly from user-supplied launch info (e.g., the user reads a README aloud). Use ONLY when discover_capability has no matching entry. BEFORE setting user_confirmed=true, ALWAYS read the proposed slug, command, args, and env back to the user verbatim and wait for an explicit yes — the user is consenting to the exact launch configuration, not the slug. Requires user_confirmed=true. Daemon restart needed to use the server.",
        handler="skill_install",
        schema_fields={
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
    ),

    # ── SKILL / TOOL MANAGEMENT ──────────────────────────────────────────────

    ToolDef(
        name="update_tool",
        description="Update an existing dynamic tool. Provide the tool name and fields to update.",
        handler="skill_update",
        schema_fields={
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
    ),
    ToolDef(
        name="delete_tool",
        description="Delete a dynamic tool by name. Cannot delete built-in tools.",
        handler="skill_delete",
        schema_fields={
            "name": {
                "type": "string",
                "description": "Tool name to delete",
            },
        },
    ),

    # ── SKILL / CAPABILITY DISCOVERY ─────────────────────────────────────────

    ToolDef(
        name="discover_capability",
        description="Search for an installable skill or MCP server matching the user's intent. Use when the user asks for something you don't have an existing tool for.",
        handler="skill_discover",
        schema_fields={
            "intent": {
                "type": "string",
                "description": "The user's intent / transcript describing what they want. Example: 'weather in kyiv'.",
            },
            "prefer_remote": {
                "type": "boolean",
                "description": "Set true when the user explicitly asks about marketplace/online skills (e.g. 'what skills exist online', 'search the marketplace'). Skips local index and queries skillsmp.com directly. Default false.",
            },
        },
    ),
    ToolDef(
        name="revoke_capability",
        description="Uninstall a previously installed skill or MCP server by slug. User-authored skills are protected and cannot be revoked.",
        handler="skill_discover",
        schema_fields={
            "slug": {
                "type": "string",
                "description": "Slug of the skill or MCP server to uninstall.",
            },
        },
    ),

    # ── BROWSER TOOLS ────────────────────────────────────────────────────────

    ToolDef(
        name="list_browser_tabs",
        description="List all open tabs in the connected Chrome browser with their id, url, title, and active state.",
        handler="browser",
    ),
    ToolDef(
        name="read_browser_page",
        description="Read the URL, title, and text content of a browser tab via the Heare Bridge extension. Optional tab_id; defaults to the active tab.",
        handler="browser",
        schema_fields={
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID to read. Omit to use the active tab.",
            },
        },
    ),
    ToolDef(
        name="navigate_browser",
        description="Navigate a browser tab to a URL and wait for the page to load. Optional tab_id; defaults to the active tab.",
        handler="browser",
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
    ),
    ToolDef(
        name="click_in_browser",
        description="Click an element in a browser tab identified by a CSS selector. Optional tab_id; defaults to the active tab.",
        handler="browser",
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
    ),
    ToolDef(
        name="fill_in_browser",
        description="Fill a form field in a browser tab identified by a CSS selector. Optional tab_id; defaults to the active tab.",
        handler="browser",
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
    ),
    ToolDef(
        name="extract_in_browser",
        description="Extract matching DOM elements (tag, text, attrs) from a browser tab by CSS selector. Optional tab_id; defaults to the active tab.",
        handler="browser",
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
    ),
    ToolDef(
        name="open_browser_tab",
        description="Open a new tab in the connected Chrome browser and navigate it to the given URL.",
        handler="browser",
        schema_fields={
            "url": {
                "type": "string",
                "description": "URL to open in a new tab (must start with http:// or https://).",
            },
        },
    ),
    ToolDef(
        name="activate_browser_tab",
        description="Bring an existing browser tab to the foreground without changing its URL or reloading it. Optional tab_id; defaults to the active tab.",
        handler="browser",
        schema_fields={
            "tab_id": {
                "type": "integer",
                "description": "Chrome tab ID to bring to the foreground. Omit to use the currently-active tab (no-op in that case).",
            },
        },
    ),

    # ── MISCELLANEOUS TOOLS ──────────────────────────────────────────────────

    ToolDef(
        name="workflow",
        description="Execute a multi-step action sequence. Provide a list of tools to call in order. Each step waits for the previous one to complete.",
        handler="batch_op",
        schema_fields={
            "steps": {"type": "array", "items": {"type": "object"}, "description": "List of actions with tool name and args."},
        },
        required=["steps"],
    ),
    ToolDef(
        name="mute_bot",
        description="Mute or unmute the bot's voice output. When muted, the bot hears but does not speak.",
        handler="mute_bot",
        schema_fields={
            "muted": {"type": "boolean", "description": "True to mute, False to unmute."},
        },
        required=["muted"],
    ),
    ToolDef(
        name="mute_mic",
        description="Mute or unmute the microphone input. When muted, the bot cannot hear anything.",
        handler="mute_mic",
        schema_fields={
            "muted": {"type": "boolean", "description": "True to mute, False to unmute."},
        },
        required=["muted"],
    ),
    ToolDef(
        name="audio_input",
        description="Switch the audio input device (microphone). Provide the device name or substring to match.",
        handler="audio_device",
        schema_fields={
            "name": {"type": "string", "description": "Device name or substring to match (e.g., 'AirPods Pro')."},
        },
        required=["name"],
    ),
    ToolDef(
        name="audio_output",
        description="Switch the audio output device (speakers). Provide the device name or substring to match.",
        handler="audio_device",
        schema_fields={
            "name": {"type": "string", "description": "Device name or substring to match (e.g., 'AirPods Pro')."},
        },
        required=["name"],
    ),
    ToolDef(
        name="create_archive",
        description="Create tar or zip archive from files/directories with compression options",
        handler="misc",
        schema_fields={
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
    ),
    ToolDef(
        name="extract_archive",
        description="Extract tar or zip archive to a directory with overwrite options",
        handler="misc",
        schema_fields={
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
    ),
    ToolDef(
        name="batch_operation",
        description="Perform operations on multiple files matching a pattern (delete, copy, move, list, archive)",
        handler="misc",
        schema_fields={
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
    ),
    ToolDef(
        name="add_favorite",
        description="Add a directory to favorites list",
        handler="misc",
        schema_fields={
            "path": {
                "type": "string",
                "description": "Directory path to add to favorites",
            },
            "label": {
                "type": "string",
                "description": "Optional label for the favorite location",
            },
        },
    ),
    ToolDef(
        name="set_view_preference",
        description="Set display preferences (show_hidden, detail_level, sort_by, sort_order)",
        handler="misc",
        schema_fields={
            "key": {
                "type": "string",
                "description": "Preference key (show_hidden, detail_level, sort_by, sort_order)",
            },
            "value": {
                "type": "string",
                "description": "Value to set (string, boolean, or integer)",
            },
        },
    ),
    ToolDef(
        name="show_profile",
        description="Show current user profile settings and preferences",
        handler="misc",
        schema_fields={
            "section": {
                "type": "string",
                "enum": ["all", "preferences", "favorites", "history"],
                "description": "Profile section to show (default: all)",
            },
        },
    ),

]


# ============================================================================
# CONVENIENCE ACCESSORS
# ============================================================================

def get_tool(name: str) -> ToolDef | None:
    """Look up a tool definition by name."""
    for t in TOOLS:
        if t.name == name:
            return t
    return None


def get_tool_names() -> list[str]:
    """Return every tool name in canonical order."""
    return [t.name for t in TOOLS]


def get_handler_types() -> list[str]:
    """Return the set of handler types used by the built-in tools."""
    return sorted({t.handler for t in TOOLS})


def get_tools_by_handler(handler: str) -> list[ToolDef]:
    """Return all tools dispatched to a given handler type."""
    return [t for t in TOOLS if t.handler == handler]


__all__ = [
    "ToolDef",
    "TOOLS",
    "get_tool",
    "get_tool_names",
    "get_handler_types",
    "get_tools_by_handler",
]
