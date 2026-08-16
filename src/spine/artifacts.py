"""Where a session's document is cleaned and where it lands on disk.

Both halves used to live inside a closure in the composition root, which
made the only interesting part — what a model actually returns when you
ask it for a markdown document — impossible to test without building a
whole engine. Models wrap the thing they were asked to produce in a code
fence more often than not, and one that never gets stripped turns every
artifact into a quote of itself.

stdlib only; the writer is the single line of I/O.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_FENCE_OPEN = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n")
_FENCE_CLOSE = re.compile(r"\n```\s*$")
# A filename is a handle, not a sentence: strip what a filesystem (or a
# human reading `ls`) would rather not see, keeping Cyrillic intact.
_UNSAFE_IN_NAME = re.compile(r"[^\wЀ-ӿ-]+", re.UNICODE)


def strip_code_fence(md: str) -> str:
    """Return the document itself, not a quotation of it.

    Only a fence wrapping the WHOLE text is removed: a fenced code block
    inside a protocol («ось команда: ```df -h```») is content and stays.
    """
    text = (md or "").strip()
    if not text.startswith("```"):
        return text
    without_open = _FENCE_OPEN.sub("", text, count=1)
    if without_open == text:  # opening fence had no newline: not a wrapper
        return text
    closed = _FENCE_CLOSE.sub("", without_open)
    if closed == without_open and not without_open.endswith("```"):
        # Opened but never closed — the model was cut off. Keep the body.
        return without_open.strip()
    return closed.rstrip().removesuffix("```").rstrip()


def artifact_name(role_name: str, now: datetime | None = None) -> str:
    """`2026-08-16-1425-мітинг.md`, safe to write and easy to read."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d-%H%M")
    safe = _UNSAFE_IN_NAME.sub("-", (role_name or "роль").strip()).strip("-")
    return f"{stamp}-{safe or 'роль'}.md"


def save_artifact(
    directory: Path | str, role_name: str, md: str, now: datetime | None = None
) -> str:
    """Write the cleaned document and return its path."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / artifact_name(role_name, now)
    path.write_text(strip_code_fence(md) + "\n", "utf-8")
    return str(path)


__all__ = ["strip_code_fence", "artifact_name", "save_artifact"]
