"""What is said in a language, as tables and rules: which words stop the
assistant, which mean yes, which reply is a promise. The core is handed
one of these and holds no words of its own."""

from __future__ import annotations

from types import ModuleType

from heare.language import en, uk


def for_language(code: str | None) -> ModuleType:
    """The words for the chosen language; Ukrainian when it is not English."""
    return en if (code or "").strip().lower().startswith("en") else uk
