"""What a task changed: the files, the patches, and one sentence about them.

The server knows exactly what one message touched (it keeps the snapshot),
so nothing here reads the disk: it shapes the answer into a card the page
can draw and a sentence the voice can say. Numbers are counted from the
diff, never from the server's ``summary`` — that one lags behind.
"""

from __future__ import annotations

import html as htmllib
import re
from pathlib import Path

from heare.core.i18n import _

# How many files are listed and patched in full; the rest are counted. A
# card is drawn in the chat, not scrolled like a terminal.
FILES = 6
# Of one patch: a change that fits is shown whole, a huge one is cut.
PATCH_CHARS = 1800

# What a person does not want to see as «work»: caches, dependencies, and
# files that changed in name only (built bytecode, a lock's mtime).
_NOISE = re.compile(
    r"(^|/)(__pycache__|node_modules|\.venv|venv|\.git|\.mypy_cache|"
    r"\.pytest_cache|\.ruff_cache|\.idea|\.DS_Store)(/|$)"
    r"|\.(pyc|pyo|so|dylib)$|\.DS_Store$")


def relevant(files: list[dict]) -> list[dict]:
    """The changes worth showing: real edits, not caches and bytecode."""
    kept = []
    for found in files:
        path = str(found.get("file") or "")
        if _NOISE.search(path):
            continue
        if not (int(found.get("additions") or 0) + int(found.get("deletions") or 0)):
            continue
        kept.append(found)
    return kept


def totals(files: list[dict]) -> tuple[int, int]:
    """The added and removed lines over every changed file."""
    files = relevant(files)
    added = sum(int(found.get("additions") or 0) for found in files)
    removed = sum(int(found.get("deletions") or 0) for found in files)
    return added, removed


def said(files: list[dict]) -> str:
    """One sentence, fit to be heard: what changed and by how much."""
    files = relevant(files)
    if not files:
        return ""
    added, removed = totals(files)
    if len(files) == 1:
        name = Path(str(files[0].get("file") or "")).name
        return _("Changed {name}: +{added} −{removed}.").format(
            name=name, added=added, removed=removed)
    # No count in the spoken line: a number before a noun needs the case to
    # agree, and a voice that says «2 файлів» is worse than one that does not
    # count. The card has the count.
    return _("Changed: +{added} −{removed}.").format(
        added=added, removed=removed)


def title(files: list[dict]) -> str:
    """The card's own name: the file when there is one, «Зміни» when more."""
    files = relevant(files)
    if not files:
        return _("Changes")
    if len(files) == 1:
        return Path(str(files[0].get("file") or "")).name
    return _("Changes")


def card(files: list[dict]) -> dict:
    """The card a hand shows: one row per file, then the patches.

    Every file name and every patch goes through ``escape``: a diff can
    contain anything the agent touched, and the page must not read it as
    markup.
    """
    files = relevant(files)
    rows: list[str] = []
    patches: list[str] = []
    for found in files[:FILES]:
        name = htmllib.escape(str(found.get("file") or ""))
        added = int(found.get("additions") or 0)
        removed = int(found.get("deletions") or 0)
        rows.append(
            f'<div class="kv"><span>{name}</span><b>+{added} −{removed}</b></div>')
        patch = str(found.get("patch") or "")
        if len(patch) > PATCH_CHARS:
            patch = patch[:PATCH_CHARS] + "\n…"
        if patch:
            patches.append(f"<pre><code>{htmllib.escape(patch)}</code></pre>")
    if len(files) > FILES:
        more = _("… and {count} more").format(count=len(files) - FILES)
        rows.append(f'<div class="kv"><span>{more}</span><b></b></div>')
    return {
        "kind": "html",
        "title": title(files),
        "say": said(files),
        "html": "".join(rows) + "".join(patches),
    }
