"""make live and make rollback: a marker only for a release that ran, and a
way back to the last one that did — not to the release before it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("live", ROOT / "scripts/live.py")
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)


def _release(root: Path, name: str, healthy: bool = False) -> Path:
    path = root / name
    (path / "heare.app").mkdir(parents=True)
    (path / "release.json").write_text(json.dumps({
        "commit": name.split("-")[-1], "dirty": False, "subject": name,
    }), encoding="utf-8")
    if healthy:
        (path / live.HEALTHY).write_text("2026-10-29T00:00:00", encoding="utf-8")
    return path


@pytest.fixture
def releases(tmp_path, monkeypatch):
    root = tmp_path / "releases"
    root.mkdir()
    monkeypatch.setattr(live, "RELEASES", root)
    monkeypatch.setattr(live, "LIVE", root / "live")
    monkeypatch.setattr(live, "INSTALLED", root / "installed")
    return root


def test_a_failed_live_keeps_the_previous_marker_and_no_health_note(releases, monkeypatch):
    old = _release(releases, "20261028-100000-aaaaaaa", healthy=True)
    new = _release(releases, "20261029-100000-bbbbbbb")
    live.LIVE.write_text(old.name, encoding="utf-8")
    monkeypatch.setattr(live, "HOME", releases.parent)
    monkeypatch.setattr(live, "wanted_parts", lambda: {})

    def install(release: Path) -> None:
        live.INSTALLED.write_text(release.name, encoding="utf-8")

    monkeypatch.setattr(live, "install", install)
    monkeypatch.setattr(live, "start", lambda: None)
    monkeypatch.setattr(live, "wait", lambda before: False)
    monkeypatch.setattr(live, "falls_since", lambda offsets: [])

    assert live.put_live(new, "оновлюю") == 1
    assert live.live_name() == old.name
    assert live.installed_name() == new.name
    assert not (new / live.HEALTHY).exists()


def test_a_healthy_live_writes_both_notes(releases, monkeypatch):
    old = _release(releases, "20261028-100000-aaaaaaa", healthy=True)
    new = _release(releases, "20261029-100000-bbbbbbb")
    live.LIVE.write_text(old.name, encoding="utf-8")
    monkeypatch.setattr(live, "HOME", releases.parent)
    monkeypatch.setattr(live, "wanted_parts", lambda: {})
    monkeypatch.setattr(live, "install", lambda release: live.INSTALLED.write_text(
        release.name, encoding="utf-8"))
    monkeypatch.setattr(live, "start", lambda: None)
    monkeypatch.setattr(live, "wait", lambda before: True)
    monkeypatch.setattr(live, "falls_since", lambda offsets: [])

    assert live.put_live(new, "оновлюю") == 0
    assert live.live_name() == new.name
    assert (new / live.HEALTHY).exists()


def test_rollback_after_a_failed_live_returns_to_the_last_good_release(releases, monkeypatch):
    good = _release(releases, "20261028-100000-aaaaaaa", healthy=True)
    _release(releases, "20261029-100000-bbbbbbb")
    live.LIVE.write_text(good.name, encoding="utf-8")
    live.INSTALLED.write_text("20261029-100000-bbbbbbb", encoding="utf-8")
    chosen: list[Path] = []
    monkeypatch.setattr(live, "put_live", lambda release, why: chosen.append(release) or 0)

    assert live.rollback() == 0
    assert chosen == [good]


def test_rollback_returns_to_the_marker_when_a_newer_release_failed(releases, monkeypatch):
    good = _release(releases, "20261028-100000-aaaaaaa", healthy=True)
    _release(releases, "20261029-100000-bbbbbbb")
    live.LIVE.write_text(good.name, encoding="utf-8")
    chosen: list[Path] = []
    monkeypatch.setattr(live, "put_live", lambda release, why: chosen.append(release) or 0)

    assert live.rollback() == 0
    assert chosen == [good]


def test_rollback_after_a_healthy_live_steps_back_one(releases, monkeypatch):
    first = _release(releases, "20261028-100000-aaaaaaa", healthy=True)
    _release(releases, "20261028-110000-bbbbbbb", healthy=True)
    last = _release(releases, "20261029-100000-ccccccc", healthy=True)
    live.LIVE.write_text(last.name, encoding="utf-8")
    live.INSTALLED.write_text(last.name, encoding="utf-8")
    chosen: list[Path] = []
    monkeypatch.setattr(live, "put_live", lambda release, why: chosen.append(release) or 0)

    assert live.rollback() == 0
    assert chosen and chosen[0].name == "20261028-110000-bbbbbbb"
    assert chosen[0] != first


def test_rollback_without_health_notes_uses_the_releases_before_live(releases, monkeypatch):
    _release(releases, "20261028-100000-aaaaaaa")
    previous = _release(releases, "20261028-110000-bbbbbbb")
    last = _release(releases, "20261029-100000-ccccccc")
    live.LIVE.write_text(last.name, encoding="utf-8")
    live.INSTALLED.write_text(last.name, encoding="utf-8")
    chosen: list[Path] = []
    monkeypatch.setattr(live, "put_live", lambda release, why: chosen.append(release) or 0)

    assert live.rollback() == 0
    assert chosen == [previous]


def test_rollback_by_a_hand_made_name_does_not_index_the_clock(releases, monkeypatch):
    made = _release(releases, "handmade")
    live.LIVE.write_text(made.name, encoding="utf-8")
    live.INSTALLED.write_text(made.name, encoding="utf-8")
    chosen: list[Path] = []
    monkeypatch.setattr(live, "put_live", lambda release, why: chosen.append(release) or 0)

    assert live.rollback("handmade") == 0
    assert chosen == [made]
