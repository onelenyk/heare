"""The release script's arithmetic, on a copy: no git, no tags."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def _copy(tmp_path: Path) -> Path:
    for path in release.carriers(ROOT):
        target = tmp_path / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
    return tmp_path


def test_one_version_is_written_everywhere_it_is_carried(tmp_path):
    repo = _copy(tmp_path)
    for path in release.carriers(repo):
        assert release.rewrite(path, "7.8.9")
    assert release.current(repo) == "7.8.9"
    site = (repo / "site/index.html").read_text(encoding="utf-8")
    assert 'const VERSION = "7.8.9";' in site and "v7.8.9</span>" in site
    # A dependency's lower bound is not the package's version.
    voice = (repo / "packages/heare-voice/pyproject.toml").read_text(encoding="utf-8")
    assert "pywebrtc-audio>=0.1.0" in voice


def test_the_packages_agree_today():
    assert release.SEMVER.match(release.current(ROOT))


def test_a_version_must_be_x_y_z():
    assert release.parse("1.10.0") > release.parse("1.9.9")
    with pytest.raises(SystemExit):
        release.parse("1.2")
