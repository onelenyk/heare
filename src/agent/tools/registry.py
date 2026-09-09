"""The tool registry — a view over src/agent/tools/system.py, not a copy.

There used to be two tables. This file held one, hand-maintained; system.py
held the other, and system.py's is the one the worker turns into schemas,
so it is the one the model sees. The May refactor that introduced system.py
said in its own plan «update registry.py, schemas.py, direct.py — keep
backward compat or delete», kept backward compat, and left two tables to
drift.

They drifted. Measured 9 September 2026: system.py had 64 tools, this file
53. Twelve names the model can be offered were unknown here — delegate,
look_at_screen, read_display, clear_display, mute_mic, mute_bot, volume,
mic_gain, sidetone, vad_sensitivity, audio_input, audio_output — so the
capability index, which builds from this table, could not find any of them.
Forty-one of the 53 shared names carried a different description: the
copies here were the pre-compaction originals, frozen in May.

There is one table now. What is left in this file is what system.py has no
opinion about: the runtime registry for tools the model creates at request
time (``create_tool``), and the predicates asked of the two together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Tool:
    """One tool, as the capability index and the dynamic registry see it.

    Static tools are projected from ``system.ToolDef``; dynamic ones are
    built here. No ``sdk_name`` and no ``execution`` column: the first fed
    ``get_sdk_tools()`` -> ``DEFAULT_SDK_ALLOWED_TOOLS`` ->
    ``Settings.get_sdk_allowed_tools()``, which nothing called, and the
    second answered a question — "does this process run it itself?" — that
    is a rule about ``mcp__`` names, not a column to keep in sync by hand.
    """

    name: str
    description: str
    enabled: bool = True


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
            errors.append(
                "name must start with a letter and contain only letters, numbers, underscores"
            )

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
# STATIC TOOLS — projected from the one table, at import
# ============================================================================


def _static_tools() -> dict[str, Tool]:
    """``system.TOOLS``, as ``Tool`` objects.

    A plain import rather than a deferred one: system.py imports nothing
    from this module, so there is no cycle to route around.
    """
    from src.agent.tools.system import TOOLS as _DEFS

    return {
        d.name: Tool(name=d.name, description=d.description, enabled=d.enabled)
        for d in _DEFS
    }


TOOLS: dict[str, Tool] = _static_tools()


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
# DERIVED — asked of the two tables together
# ============================================================================


def get_enabled_tools() -> set[str]:
    """Every enabled tool name, static and dynamic."""
    static = {t.name for t in TOOLS.values() if t.enabled}
    dynamic = {t.name for t in _DYNAMIC_TOOLS.values() if t.enabled}
    return static | dynamic


def get_tool(name: str) -> Tool | None:
    """One tool by name, static or dynamic."""
    return TOOLS.get(name) or _DYNAMIC_TOOLS.get(name)


def is_mcp_tool(name: str) -> bool:
    """Is this an MCP tool (``mcp__<server>__<action>``)?"""
    return name.startswith("mcp__") and name.count("__") >= 2


def get_direct_tools() -> set[str]:
    """Tools this process executes itself — everything but MCP."""
    return {name for name in get_enabled_tools() if not is_mcp_tool(name)}
