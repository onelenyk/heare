"""Watch dashboard - Live status TUI for heare daemon.

Migrated from rich.live to Textual for real scrolling and better interactivity.
"""
from __future__ import annotations

# Temporary: re-export legacy functions for backward compatibility during migration
# These will be removed in US-010 after tests are migrated
from ._legacy import (
    _activity_table,
    _bot_table,
    _build_header,
    _counts,
    _current_mode,
    _current_provider,
    _daemon_status,
    _did_table,
    _fetch,
    _fmt_time,
    _load_speaker_labels,
    _open_db,
    _speaker_style,
    _truncate,
    _you_table,
)


def run_watch(settings: Any, interval: float, once: bool = False) -> int:
    """Run the watch dashboard.

    Args:
        settings: Application settings
        interval: Refresh interval in seconds
        once: If True, print snapshot once and exit (bypass TUI)

    Returns:
        Exit code (0 for success)
    """
    # TODO: Implement in US-008
    # For now, use legacy implementation
    from ._legacy import run_watch as _legacy_run_watch

    return _legacy_run_watch(settings, interval, once)
