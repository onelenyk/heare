"""Unified tool registry — single source of truth for all agent tools.

Defines available tools with their metadata:
- name: lowercase identifier used in intents/prompts
- sdk_name: CamelCase identifier for claude-agent-sdk
- execution: "direct" (fast) | "claude" (needs reasoning) | "workflow" (special)
- description: human-readable purpose
- enabled: whether the tool is active

All other lists (ALLOWED_TOOLS, SIMPLE_TOOLS, agent_sdk_allowed_tools, etc.)
are derived from this registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExecutionType = Literal["direct", "claude", "workflow", "mcp"]


@dataclass(frozen=True)
class Tool:
    """Definition of a single tool capability."""

    name: str  # lowercase: "bash", "read", etc.
    sdk_name: str  # CamelCase for SDK: "Bash", "Read", etc.
    execution: ExecutionType  # how it's executed
    description: str  # what it does
    enabled: bool = True  # can be toggled


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
    "edit": Tool(
        name="edit",
        sdk_name="Edit",
        execution="claude",
        description="Edit files with diff/apply (requires Claude reasoning)",
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
    "re_enroll": Tool(
        name="re_enroll",
        sdk_name="ReEnroll",  # Not exposed to SDK
        execution="direct",
        description="Re-train the voice recognition model",
        enabled=True,
    ),
    "list_profiles": Tool(
        name="list_profiles",
        sdk_name="ListProfiles",  # Not exposed to SDK
        execution="direct",
        description="Show all voice profiles with details",
        enabled=True,
    ),
    "create_profile": Tool(
        name="create_profile",
        sdk_name="CreateProfile",  # Not exposed to SDK
        execution="direct",
        description="Create a new voice profile",
        enabled=True,
    ),
    "delete_profile": Tool(
        name="delete_profile",
        sdk_name="DeleteProfile",  # Not exposed to SDK
        execution="direct",
        description="Delete a speaker profile by ID",
        enabled=True,
    ),
    "rename_profile": Tool(
        name="rename_profile",
        sdk_name="RenameProfile",  # Not exposed to SDK
        execution="direct",
        description="Rename a speaker profile",
        enabled=True,
    ),
}


# ============================================================================
# DERIVED LISTS — generated from TOOLS, do not edit manually
# ============================================================================

def get_enabled_tools() -> set[str]:
    """Get all enabled tool names (lowercase)."""
    return {t.name for t in TOOLS.values() if t.enabled}


def get_sdk_tools() -> list[str]:
    """Get CamelCase tool names for claude-agent-sdk allowlist."""
    return [t.sdk_name for t in TOOLS.values() if t.enabled and t.execution != "workflow"]


def get_direct_tools() -> set[str]:
    """Get tools that execute directly (fast path, no Claude needed)."""
    return {t.name for t in TOOLS.values() if t.enabled and t.execution == "direct"}


def get_claude_tools() -> set[str]:
    """Get tools that require Claude reasoning."""
    return {t.name for t in TOOLS.values() if t.enabled and t.execution == "claude"}


def get_tool(name: str) -> Tool | None:
    """Get a tool by name, or None if not found."""
    return TOOLS.get(name)


def is_tool_allowed(name: str) -> bool:
    """Check if a tool name is allowed and enabled.

    Returns True for:
    - Tools in the registry that are enabled
    - MCP tools (mcp__<server>__<action>)
    """
    tool = TOOLS.get(name)
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
COMPLEX_TOOLS = get_claude_tools() | {t for t in TOOLS if is_mcp_tool(t)}
