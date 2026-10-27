"""What the person hears and sees, in their language: gettext over one catalog.

The key is the English text itself — ``_("Stopped.")`` — so a line with no
translation is still a line, in English, never a broken key. What a model
reads is not passed through here: it is English everywhere. The person's
own words are never translated.

The language is looked up on every call, not fixed at start: a choice made
on the page counts from the next line. Whoever owns the settings tells this
module where to look (``choose``); the core reads no settings itself.
"""

from __future__ import annotations

import gettext
from collections.abc import Callable
from functools import cache
from pathlib import Path

LOCALE = Path(__file__).resolve().parent / "locale"
DOMAIN = "heare"
LANGUAGES = ("uk", "en")
DEFAULT = "uk"

def _default() -> str:
    return DEFAULT


_chosen: Callable[[], str] = _default


def choose(language: Callable[[], str]) -> None:
    """Where the language comes from, asked again on every line."""
    global _chosen
    _chosen = language


def language() -> str:
    try:
        found = str(_chosen() or "").strip().lower()
    except Exception:  # noqa: BLE001 - a broken setting must not silence a reply
        return DEFAULT
    return found if found in LANGUAGES else DEFAULT


@cache
def _catalog(lang: str) -> gettext.NullTranslations:
    return gettext.translation(DOMAIN, LOCALE, [lang], fallback=True)


def _(text: str) -> str:
    return _catalog(language()).gettext(text)


def N_(text: str) -> str:
    """Marks a line for the catalog where it is defined; ``_`` says it later."""
    return text
