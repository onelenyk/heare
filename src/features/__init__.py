"""Optional capabilities, each whole in its own directory.

This is the one list of what is installed. Before it, the answer to "what
features does this assistant have?" was spread across a 720-line wiring
function, a flags table, a tool table and a dashboard card, and no two of
them agreed — which is how a browser bridge could be switched on,
advertised and paired for months without ever being constructed.

A feature lands here only with its whole contract (see src/plugins.py):
what it costs to switch off, how it attaches, and a health() that can say
whether it is actually working. The order they move in, and why, is in
docs/archive/2026-09-16/core-and-plugins.md.
"""
from __future__ import annotations


def installed() -> tuple:
    """Every plugin, imported on demand so this list costs nothing to read."""
    from src.features.usage import PLUGIN as usage

    return (usage,)


def install() -> None:
    """Put every installed plugin in the register. Safe to call repeatedly."""
    from src.plugins import install as _install

    _install(*installed())
