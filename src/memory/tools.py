"""LLM tool handler functions for memory operations.

Each handler takes (backend: MemoryBackend, args_str: str) and returns a dict
with "success", optional "spoken" (dict with en/uk keys), and tool-specific
fields.  Follows the handler contract established in src/agent/tools/direct.py.
"""

from __future__ import annotations

import json
from typing import Any

from src.memory.base import MemoryBackend, MemoryEntry, MemoryType


async def remember(backend: MemoryBackend, args_str: str) -> dict[str, Any]:
    """Parse JSON args {"type": "fact", "content": "..."}, call backend.store()."""
    try:
        params = json.loads(args_str)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Invalid JSON arguments",
            "spoken": {
                "en": "Invalid arguments.",
                "uk": "Неправильні аргументи.",
            },
        }

    type_str = str(params.get("type", "")).strip()
    content = str(params.get("content", "")).strip()
    if not type_str or not content:
        return {
            "success": False,
            "error": "type and content are required",
            "spoken": {
                "en": "I need both type and content to remember.",
            },
        }

    try:
        memory_type = MemoryType(type_str)
    except ValueError:
        return {
            "success": False,
            "error": f"Invalid type: {type_str}",
            "spoken": {
                "en": f"Unknown memory type: {type_str}",
            },
        }

    entry = MemoryEntry(
        id="",
        type=memory_type,
        content=content,
        source="user_stated",
    )
    memory_id = await backend.store(entry)

    type_names: dict[str, str] = {
        "fact": "факт",
        "preference": "вподобання",
        "decision": "рішення",
        "event": "подію",
    }
    return {
        "success": True,
        "memory_id": memory_id,
        "spoken": {
            "en": f"Remembered {type_str}.",
            "uk": f"Запам'ятала {type_names.get(type_str, type_str)}.",
        },
    }


async def recall(backend: MemoryBackend, args_str: str) -> dict[str, Any]:
    """Parse {"query": "..."}, call backend.search()."""
    try:
        params = json.loads(args_str)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Invalid JSON",
            "spoken": {"en": "Invalid arguments."},
        }

    query = str(params.get("query", "")).strip()
    if not query:
        return {
            "success": False,
            "error": "query is required",
            "spoken": {"en": "What should I recall?"},
        }

    results = await backend.search(query, limit=5)
    formatted = [
        {"id": r.id, "type": r.type.value, "content": r.content} for r in results
    ]

    if formatted:
        count = len(formatted)
        return {
            "success": True,
            "results": formatted,
            "count": count,
            "spoken": {
                "en": f"Found {count} memories.",
                "uk": f"Знайшла {count} спогадів.",
            },
        }
    else:
        return {
            "success": True,
            "results": [],
            "count": 0,
            "spoken": {
                "en": "No memories found.",
                "uk": "Нічого не знайшла.",
            },
        }


async def forget(backend: MemoryBackend, args_str: str) -> dict[str, Any]:
    """Parse {"memory_id": "..."}, call backend.forget()."""
    try:
        params = json.loads(args_str)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "Invalid JSON",
            "spoken": {"en": "Invalid arguments."},
        }

    memory_id = str(params.get("memory_id", "")).strip()
    if not memory_id:
        return {
            "success": False,
            "error": "memory_id is required",
            "spoken": {"en": "Which memory should I forget?"},
        }

    ok = await backend.forget(memory_id)
    if ok:
        return {
            "success": True,
            "spoken": {"en": "Forgotten.", "uk": "Забула."},
        }
    else:
        return {
            "success": False,
            "error": "Memory not found",
            "spoken": {
                "en": "Memory not found.",
                "uk": "Не знайшла такий спогад.",
            },
        }


async def memory_status(backend: MemoryBackend, args_str: str) -> dict[str, Any]:
    """Call backend.stats(). Ignores args_str."""
    _ = args_str  # unused
    stats = await backend.stats()
    total = stats.get("total", 0)
    by_type: dict[str, int] = stats.get("by_type", {})
    by_type_str = (
        ", ".join(f"{k}: {v}" for k, v in by_type.items()) if by_type else "none"
    )
    return {
        "success": True,
        "stats": stats,
        "spoken": {
            "en": f"{total} total memories ({by_type_str}).",
            "uk": f"{total} спогадів ({by_type_str}).",
        },
    }
