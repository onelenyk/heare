"""Recall: what was actually said, searched in memory."""

from __future__ import annotations

from heare.core.forms import Memory


async def run(memory: Memory, arguments: dict) -> str:
    query = str(arguments.get("query", "")).strip()
    if not query:
        return "Що саме згадати?"
    return await memory.recall(
        query, str(arguments.get("when", "") or "")
    )
