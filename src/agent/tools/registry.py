"""Unified tool registry — single source of truth for all agent tools.

Defines available tools with their metadata:
- name: lowercase identifier used in intents/prompts
- sdk_name: CamelCase identifier for claude-agent-sdk
- execution: "direct" (fast) | "workflow" (special)
- description: human-readable purpose
- enabled: whether the tool is active

All other lists (ALLOWED_TOOLS, SIMPLE_TOOLS, agent_sdk_allowed_tools, etc.)
are derived from this registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ExecutionType = Literal["direct", "workflow", "mcp"]


@dataclass(frozen=True)
class Tool:
    """Definition of a single tool capability."""

    name: str  # lowercase: "bash", "read", etc.
    sdk_name: str  # CamelCase for SDK: "Bash", "Read", etc.
    execution: ExecutionType  # how it's executed
    description: str  # what it does
    enabled: bool = True  # can be toggled


@dataclass(frozen=True)
class ToolDefinition:
    """Schema for a dynamic tool definition.

    Used when the LLM creates a new tool at runtime.
    """

    name: str  # lowercase, no spaces
    description: str  # what the tool does
    arguments: dict[str, dict]  # JSON schema format
    # The legacy "python" type used eval() and was removed for security.
    implementation_type: Literal["bash", "fetch"]  # how to execute
    implementation: str  # command/url

    def validate(self) -> list[str]:
        """Validate the tool definition, returning a list of errors."""
        errors: list[str] = []

        if not self.name:
            errors.append("name is required")
        elif not self.name.islower():
            errors.append("name must be lowercase")
        elif not re.match(r"^[a-z][a-z0-9_]*$", self.name):
            errors.append("name must start with a letter and contain only letters, numbers, underscores")

        if not self.description:
            errors.append("description is required")

        if self.implementation_type == "python":
            errors.append(
                "implementation_type 'python' is no longer supported "
                "(use 'bash' or 'fetch' instead)"
            )
        elif self.implementation_type not in ("bash", "fetch"):
            errors.append(f"invalid implementation_type: {self.implementation_type}")

        if not self.implementation:
            errors.append("implementation is required")

        if not self.arguments:
            errors.append("arguments schema is required")
        elif not isinstance(self.arguments, dict):
            errors.append("arguments must be a dict")

        return errors


# ============================================================================
# CORE TOOL REGISTRY — add new tools here
# ============================================================================

TOOLS: dict[str, Tool] = {
    "bash": Tool(
        name="bash",
        sdk_name="Bash",
        execution="direct",
        description="Execute shell commands in the workspace directory",
        enabled=True,
    ),
    "read": Tool(
        name="read",
        sdk_name="Read",
        execution="direct",
        description="Read file contents from the workspace",
        enabled=True,
    ),
    "write": Tool(
        name="write",
        sdk_name="Write",
        execution="direct",
        description="Write content to a file (format: 'filepath: content')",
        enabled=True,
    ),
    "web_fetch": Tool(
        name="web_fetch",
        sdk_name="WebFetch",
        execution="direct",
        description="Fetch and return content from a URL",
        enabled=True,
    ),
    "web_search": Tool(
        name="web_search",
        sdk_name="WebSearch",
        execution="direct",
        description="Search the web (uses Serper.dev if key available, else DuckDuckGo)",
        enabled=True,
    ),
    "workflow": Tool(
        name="workflow",
        sdk_name="Workflow",
        execution="workflow",
        description="Execute saved multi-step action sequences",
        enabled=True,
    ),
    # CCS-05b: stable identifier for cancellation rows in the action log.
    # The cancel "tool" never executes — IntentQueue.cancel_active /
    # ActionWorker.cancel_in_flight short-circuit before dispatch — but
    # registering it keeps the action_log shape consistent
    # (tool='cancel' rather than empty string) and avoids tripping the
    # is_tool_allowed allowlist if a future code path submits it.
    "cancel": Tool(
        name="cancel",
        sdk_name="Cancel",  # No-op SDK mapping — tool never reaches the SDK.
        execution="direct",
        description="Cancel the in-flight action and drain pending intents",
        enabled=True,
    ),
    # CRUD tools for dynamic tool management
    "create_tool": Tool(
        name="create_tool",
        sdk_name="CreateTool",
        execution="direct",
        description="Create a new tool dynamically. Provide: name, description, arguments (JSON schema), implementation type (bash/fetch/python), and implementation string.",
        enabled=True,
    ),
    "update_tool": Tool(
        name="update_tool",
        sdk_name="UpdateTool",
        execution="direct",
        description="Update an existing dynamic tool. Provide the tool name and fields to update.",
        enabled=True,
    ),
    "delete_tool": Tool(
        name="delete_tool",
        sdk_name="DeleteTool",
        execution="direct",
        description="Delete a dynamic tool by name. Cannot delete built-in tools.",
        enabled=True,
    ),
    "list_tools": Tool(
        name="list_tools",
        sdk_name="ListTools",
        execution="direct",
        description="List all available tools, including dynamically created ones.",
        enabled=True,
    ),
    # Archive & Batch Tools
    "create_archive": Tool(
        name="create_archive",
        sdk_name="CreateArchive",
        execution="direct",
        description="Create tar or zip archive from files/directories with compression options",
        enabled=True,
    ),
    "extract_archive": Tool(
        name="extract_archive",
        sdk_name="ExtractArchive",
        execution="direct",
        description="Extract tar or zip archive to a directory with overwrite options",
        enabled=True,
    ),
    "batch_operation": Tool(
        name="batch_operation",
        sdk_name="BatchOperation",
        execution="direct",
        description="Perform operations on multiple files matching a pattern (delete, copy, move, list, archive)",
        enabled=True,
    ),
    # Profile Management Tools
    "add_favorite": Tool(
        name="add_favorite",
        sdk_name="AddFavorite",
        execution="direct",
        description="Add a directory to favorites list",
        enabled=True,
    ),
    "list_favorites": Tool(
        name="list_favorites",
        sdk_name="ListFavorites",
        execution="direct",
        description="List favorite locations with access counts",
        enabled=True,
    ),
    "set_view_preference": Tool(
        name="set_view_preference",
        sdk_name="SetViewPreference",
        execution="direct",
        description="Set display preferences (show_hidden, detail_level, sort_by, sort_order)",
        enabled=True,
    ),
    "show_profile": Tool(
        name="show_profile",
        sdk_name="ShowProfile",
        execution="direct",
        description="Show current user profile settings and preferences",
        enabled=True,
    ),
    # Agent Skills meta-tools (agentskills.io format)
    "list_skills": Tool(
        name="list_skills",
        sdk_name="ListSkills",
        execution="direct",
        description="List available Agent Skills. Returns skill names and one-line descriptions. Call this to discover what portable skills exist.",
        enabled=True,
    ),
    "run_skill": Tool(
        name="run_skill",
        sdk_name="RunSkill",
        execution="direct",
        description="Execute an Agent Skill by name. Skills can orchestrate multiple heare tools internally. Provide the skill name and context dict with required parameters.",
        enabled=True,
    ),
    "set_provider": Tool(
        name="set_provider",
        sdk_name="SetProvider",
        execution="direct",
        description="Switch the active LLM provider (deepseek, zai, or opencode). Change takes effect on the next user utterance.",
        enabled=True,
    ),
    "set_mode": Tool(
        name="set_mode",
        sdk_name="SetMode",
        execution="direct",
        description="Switch the agent's behavior mode: ambient (default conversational), focus (terse/fast), silent (speak only when addressed), assistant (proactive, full tools), meeting (passive note-taker, no side-effect tools). Takes effect immediately.",
        enabled=True,
    ),
    "show_text": Tool(
        name="show_text",
        sdk_name="ShowText",
        execution="direct",
        enabled=True,
        description="Show text on the display panel. Use when voice is unavailable (muted/silent) or when content is better read than heard: facts, lists, references, explanations. Speak a short pointer ('showing on screen') then call this.",
    ),
    "show_canvas": Tool(
        name="show_canvas",
        sdk_name="ShowCanvas",
        execution="direct",
        enabled=True,
        description="Render HTML/JS in the canvas panel. Use for charts, diagrams, visual demos, UI components. Speak a short pointer ('rendering chart') then call this.",
    ),
    # Capability discovery (US-007) — proactive skill/MCP install + revoke.
    "discover_capability": Tool(
        name="discover_capability",
        sdk_name="DiscoverCapability",
        execution="direct",
        description="Search for an installable skill or MCP server matching the user's intent. Use when the user asks for something you don't have an existing tool for.",
        enabled=True,
    ),
    "install_skill_tool": Tool(
        name="install_skill_tool",
        sdk_name="InstallSkillTool",
        execution="direct",
        description="Install a skill from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        enabled=True,
    ),
    "create_skill": Tool(
        name="create_skill",
        sdk_name="CreateSkill",
        execution="direct",
        description=(
            "Author a new local skill from the conversation. Use when the user "
            "asks to remember a procedure or workflow as a reusable skill. The "
            "skill body is markdown instructions the LLM will read when "
            "run_skill is later invoked. Requires user_confirmed=true after "
            "explicit voice consent."
        ),
        enabled=True,
    ),
    "stop_daemon": Tool(
        name="stop_daemon",
        sdk_name="StopDaemon",
        execution="direct",
        description=(
            "Gracefully stop the running daemon. Use ONLY when the user "
            "explicitly asks to stop / shut down / quit / kill the bot. "
            "Never call this to 'free resources' or as part of any other "
            "task. Requires user_confirmed=true after explicit voice consent."
        ),
        enabled=True,
    ),
    "restart_daemon": Tool(
        name="restart_daemon",
        sdk_name="RestartDaemon",
        execution="direct",
        description=(
            "Restart the running daemon. Use when the user asks to restart "
            "/ reload after config or code changes. This is the ONLY safe "
            "way to restart from inside the daemon — never run `make "
            "restart` or `hearectl restart` via bash, those terminate the "
            "agent without bringing it back. Requires user_confirmed=true."
        ),
        enabled=True,
    ),
    "install_mcp_server_tool": Tool(
        name="install_mcp_server_tool",
        sdk_name="InstallMcpServerTool",
        execution="direct",
        description="Install an MCP server from the marketplace by slug. Requires user_confirmed=true after explicit voice consent.",
        enabled=True,
    ),
    "register_mcp_server": Tool(
        name="register_mcp_server",
        sdk_name="RegisterMcpServer",
        execution="direct",
        description=(
            "Register an MCP server directly from user-supplied launch info "
            "(e.g., the user reads a README aloud). Use ONLY when "
            "discover_capability has no matching entry. BEFORE setting "
            "user_confirmed=true, ALWAYS read the proposed slug, command, args, "
            "and env back to the user verbatim and wait for an explicit yes — "
            "the user is consenting to the exact launch configuration, not the "
            "slug. Requires user_confirmed=true. Daemon restart needed to use "
            "the server."
        ),
        enabled=True,
    ),
    "revoke_capability": Tool(
        name="revoke_capability",
        sdk_name="RevokeCapability",
        execution="direct",
        description="Uninstall a previously installed skill or MCP server by slug. User-authored skills are protected and cannot be revoked.",
        enabled=True,
    ),
    "list_capabilities": Tool(
        name="list_capabilities",
        sdk_name="ListCapabilities",
        execution="direct",
        description="List everything the agent can call, grouped into three buckets: built_in (code-backed tools), skills (markdown procedures), mcps (external MCP servers). Optional 'category' filter: built_in | skills | mcps.",
        enabled=True,
    ),
    # Browser bridge tools (Phase 1)
    "read_browser_page": Tool(
        name="read_browser_page",
        sdk_name="ReadBrowserPage",
        execution="direct",
        description="Read the URL, title, and text content of a browser tab via the Heare Bridge extension. Optional tab_id; defaults to the active tab.",
        enabled=True,
    ),
    "list_browser_tabs": Tool(
        name="list_browser_tabs",
        sdk_name="ListBrowserTabs",
        execution="direct",
        description="List all open tabs in the connected Chrome browser with their id, url, title, and active state.",
        enabled=True,
    ),
    "click_in_browser": Tool(
        name="click_in_browser",
        sdk_name="ClickInBrowser",
        execution="direct",
        description="Click an element in a browser tab identified by a CSS selector. Optional tab_id; defaults to the active tab.",
        enabled=True,
    ),
    "fill_in_browser": Tool(
        name="fill_in_browser",
        sdk_name="FillInBrowser",
        execution="direct",
        description="Fill a form field in a browser tab identified by a CSS selector. Optional tab_id; defaults to the active tab.",
        enabled=True,
    ),
    "navigate_browser": Tool(
        name="navigate_browser",
        sdk_name="NavigateBrowser",
        execution="direct",
        description="Navigate a browser tab to a URL and wait for the page to load. Optional tab_id; defaults to the active tab.",
        enabled=True,
    ),
    "extract_in_browser": Tool(
        name="extract_in_browser",
        sdk_name="ExtractInBrowser",
        execution="direct",
        description="Extract matching DOM elements (tag, text, attrs) from a browser tab by CSS selector. Optional tab_id; defaults to the active tab.",
        enabled=True,
    ),
    "open_browser_tab": Tool(
        name="open_browser_tab",
        sdk_name="OpenBrowserTab",
        execution="direct",
        description="Open a new tab in the connected Chrome browser and navigate it to the given URL.",
        enabled=True,
    ),
    "activate_browser_tab": Tool(
        name="activate_browser_tab",
        sdk_name="ActivateBrowserTab",
        execution="direct",
        description="Bring an existing browser tab to the foreground without changing its URL or reloading it. Optional tab_id; defaults to the active tab.",
        enabled=True,
    ),
}


# ============================================================================
# RUNTIME TOOL REGISTRY — for dynamically created tools
# ============================================================================

_DYNAMIC_TOOLS: dict[str, Tool] = {}


def register_dynamic_tool(tool: Tool) -> None:
    """Register a tool at runtime."""
    _DYNAMIC_TOOLS[tool.name] = tool


def unregister_dynamic_tool(name: str) -> bool:
    """Unregister a tool at runtime. Returns True if tool existed."""
    return _DYNAMIC_TOOLS.pop(name, None) is not None


def get_tool(name: str) -> Tool | None:
    """Get tool from static or dynamic registry. Overrides the function below."""
    return TOOLS.get(name) or _DYNAMIC_TOOLS.get(name)


def get_all_tools() -> dict[str, Tool]:
    """Get all tools (static + dynamic)."""
    return {**TOOLS, **_DYNAMIC_TOOLS}


def is_dynamic_tool(name: str) -> bool:
    """Check if a tool is dynamically created."""
    return name in _DYNAMIC_TOOLS


def is_static_tool(name: str) -> bool:
    """Check if a tool is a built-in static tool."""
    return name in TOOLS


# ============================================================================
# DERIVED LISTS — generated from TOOLS, do not edit manually
# ============================================================================

def get_enabled_tools() -> set[str]:
    """Get all enabled tool names (lowercase), including dynamic tools."""
    static = {t.name for t in TOOLS.values() if t.enabled}
    dynamic = {t.name for t in _DYNAMIC_TOOLS.values() if t.enabled}
    return static | dynamic


def get_sdk_tools() -> list[str]:
    """Get CamelCase tool names for claude-agent-sdk allowlist."""
    return [t.sdk_name for t in TOOLS.values() if t.enabled and t.execution != "workflow"]


def get_direct_tools() -> set[str]:
    """Get tools that execute directly (fast path, no Claude needed)."""
    return {t.name for t in TOOLS.values() if t.enabled and t.execution == "direct"}


def is_tool_allowed(name: str) -> bool:
    """Check if a tool name is allowed and enabled.

    Returns True for:
    - Tools in the static registry that are enabled
    - Dynamic tools that are enabled
    - MCP tools (mcp__<server>__<action>)
    """
    tool = TOOLS.get(name) or _DYNAMIC_TOOLS.get(name)
    if tool is not None and tool.enabled:
        return True
    # MCP tools are always allowed if they follow the pattern
    return is_mcp_tool(name)


def is_mcp_tool(name: str) -> bool:
    """Check if a tool name is an MCP tool (mcp__<server>__<action>)."""
    return name.startswith("mcp__") and name.count("__") >= 2


def get_intent_to_sdk_mapping() -> dict[str, str]:
    """Map lowercase intent names to CamelCase SDK names."""
    return {t.name: t.sdk_name for t in TOOLS.values() if t.enabled}


def get_tool_descriptions() -> str:
    """Generate a formatted string of all enabled tools for prompts."""
    lines = []
    for tool in sorted(TOOLS.values(), key=lambda t: t.name):
        if not tool.enabled:
            continue
        lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


# ============================================================================
# BACKWARD COMPATIBILITY — deprecated aliases, remove in v0.3
# ============================================================================

# Legacy: actions.py used this directly
ALLOWED_TOOLS = get_enabled_tools()

# Legacy: actions.py used this for name mapping
INTENT_TOOL_TO_SDK = get_intent_to_sdk_mapping()

# Legacy: config.py used this for SDK
DEFAULT_SDK_ALLOWED_TOOLS = get_sdk_tools()

# Legacy: direct_tools.py used this
SIMPLE_TOOLS = get_direct_tools()
