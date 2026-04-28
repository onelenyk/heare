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

from .direct_tools import execute_direct
from .tool_registry import TOOLS, get_enabled_tools

if TYPE_CHECKING:
    from .config import Settings


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


def _edit_args(args: dict[str, Any]) -> str:
    """edit is currently a Claude-routed tool — wrap into a JSON blob."""
    return json.dumps(args)


def _url_args(args: dict[str, Any]) -> str:
    return str(args.get("url", "")).strip()


def _query_args(args: dict[str, Any]) -> str:
    return str(args.get("query", "")).strip()


def _name_args(args: dict[str, Any]) -> str:
    return str(args.get("name", "")).strip()


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
    "edit": (
        {
            "path": {
                "type": "string",
                "description": "Absolute path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find and replace.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text.",
            },
        },
        ["path", "old_string", "new_string"],
        _edit_args,
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
    return ToolsSchema(standard_tools=schemas)


_intent_id_seq = itertools.count(start=1)


def _make_handler(
    tool_name: str,
    serializer: ArgsSerializer,
    settings: "Settings | None",
    conversation_manager: Any = None,
) -> Callable[[Any], Awaitable[None]]:
    """Build a Pipecat ``FunctionCallParams`` handler for one tool.

    When ``conversation_manager`` is supplied, the handler also threads
    each invocation through ``record_action_pending`` / ``record_action_result``
    / ``record_action_cancelled`` / ``record_action_error`` so the
    conversation memory's recent-actions block stays populated for the
    next-turn LLM context (CCS-02 / CCS-04 grounding rules).
    """

    async def handler(params: Any) -> None:
        args_str = serializer(dict(params.arguments or {}))
        intent_id = next(_intent_id_seq)
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
        handler = _make_handler(name, serializer, settings, conversation_manager)
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


__all__ = [
    "build_tools_schema",
    "register_all_tools",
]
