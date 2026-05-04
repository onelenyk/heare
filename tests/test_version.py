"""Tests for src.version — __version__ ↔ pyproject.toml lockstep + app_version() shape."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from unittest.mock import patch

from src import version as version_module
from src.version import __version__, app_version


def test_version_string_is_semver() -> None:
    assert re.match(r"^\d+\.\d+\.\d+(?:[.\-+][\w.\-]+)?$", __version__), (
        f"__version__ {__version__!r} is not a valid semver"
    )


def test_version_matches_pyproject_toml() -> None:
    """``src.version.__version__`` is the runtime source of truth and
    MUST stay aligned with ``pyproject.toml::project.version`` so users
    see the same number on the dashboard, in logs, and in package
    metadata. A drift here means someone bumped one and forgot the
    other — fix by matching them."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__, (
        f"pyproject.toml ({data['project']['version']}) and "
        f"src.version.__version__ ({__version__}) disagree"
    )


def test_app_version_includes_v_prefix() -> None:
    out = app_version(include_sha=False)
    assert out.startswith("v")
    assert __version__ in out


def test_app_version_appends_git_sha_when_available() -> None:
    """When running from a git checkout we expect the short SHA in
    parens. Mocking the resolved SHA isolates this from CI runners
    that strip ``.git``."""
    with patch.object(version_module, "_GIT_SHA", "abc1234"):
        out = app_version()
    assert out == f"v{__version__} (abc1234)"


def test_app_version_omits_sha_when_unavailable() -> None:
    """Installed-package case (no .git directory) — output stays clean
    so it doesn't render ``v0.1.0 (None)`` or similar garbage."""
    with patch.object(version_module, "_GIT_SHA", None):
        out = app_version()
    assert out == f"v{__version__}"
    assert "(" not in out


def test_app_version_can_omit_sha_explicitly() -> None:
    with patch.object(version_module, "_GIT_SHA", "abc1234"):
        out = app_version(include_sha=False)
    assert out == f"v{__version__}"


def test_package_re_exports_version() -> None:
    """Convenience: ``from src import __version__`` should work so
    callers don't need to know about ``src.version``."""
    import src

    assert src.__version__ == __version__
    assert callable(src.app_version)
