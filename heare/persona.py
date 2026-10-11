"""Who the assistant is and how it talks: the text at the head of every prompt.

Kept beside the core, not in it. The core reads this through one
function; the rules themselves are a file a person can edit
(``prompts/voice_rules.txt``), and the name is the one the old app
already answers to (``~/.heare/identity.json``).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "prompts" / "voice_rules.txt"
IDENTITY = Path.home() / ".heare" / "identity.json"
_DAYS = ("понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя")


def name() -> str:
    try:
        return str(json.loads(IDENTITY.read_text(encoding="utf-8")).get("name") or "")
    except (OSError, ValueError):
        return ""


def names() -> frozenset[str]:
    """The name as a word of a command must not count: «Дока, стоп» is «стоп»."""
    return frozenset(w for w in (name().lower(), "дока", "heare") if w)


async def prompt() -> str:
    """Read again on every turn: a saved rule counts from the next reply."""
    try:
        rules = RULES.read_text(encoding="utf-8").strip()
    except OSError:
        rules = ""
    parts = [rules] if rules else []
    if called := name():
        parts.append(f"Тебе звати {called}.")
    # A model does not know the hour unless it is told.
    now = datetime.now()
    parts.append(f"Зараз {_DAYS[now.weekday()]}, {now:%d.%m.%Y}, {now:%H:%M}.")
    return "\n\n".join(parts)
