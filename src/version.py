"""Application version — single runtime source of truth.

``__version__`` is the canonical semver string and MUST stay in sync
with ``pyproject.toml::project.version``. ``test_version.py`` enforces
that, so a bump in either place will fail CI until both are aligned.

``app_version()`` returns a display string for the dashboard header /
startup log: ``"v0.1.0 (abc1234)"`` with the short git SHA appended
when running from a checkout. The SHA is captured once at import time
— restarting the daemon picks up the new SHA, but it never re-runs
``git`` per call.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

__version__ = "0.1.1"


logger = logging.getLogger("heare.version")


def _resolve_git_sha() -> str | None:
    """Return the short git SHA of the current checkout, or None.

    Best-effort: silently returns None when ``.git`` is missing
    (installed-package case) or git is unavailable. Never raises.
    """
    project_root = Path(__file__).resolve().parent.parent
    if not (project_root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git rev-parse failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


_GIT_SHA: str | None = _resolve_git_sha()


def app_version(*, include_sha: bool = True) -> str:
    """Return the user-facing version string.

    >>> app_version(include_sha=False)
    'v0.1.0'

    With a git checkout, ``include_sha=True`` (default) yields
    ``'v0.1.0 (abc1234)'``. Without ``.git`` the parens are omitted so
    the string stays clean for installed-package use.
    """
    base = f"v{__version__}"
    if include_sha and _GIT_SHA:
        return f"{base} ({_GIT_SHA})"
    return base


__all__ = ["__version__", "app_version"]
