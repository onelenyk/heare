"""The inject queue.

The pusher and the polling loop that used to live beside these were
part of the pipecat path and went with it; the queue itself is what the
dashboard, the HTTP API and the daemon all still write to.

File-queue IPC: an external process writes a message, the daemon
reads and deletes it. A crash mid-message loses nothing, because the
unread files are still on disk at the next start."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_inject_text_writes_atomic_file(tmp_path: Path):
    from src.inject import inject_text

    folder = tmp_path / "inject"
    path = inject_text(folder, "  hello world  ")

    assert path.exists()
    assert path.parent == folder
    assert path.suffix == ".txt"
    # Whitespace stripped on the way in (avoids double-stripping at consume).
    assert path.read_text() == "hello world"


def test_inject_text_rejects_empty(tmp_path: Path):
    from src.inject import inject_text

    with pytest.raises(ValueError):
        inject_text(tmp_path, "   ")


def test_drain_once_orders_chronologically(tmp_path: Path):
    from src.inject import drain as _drain_once, inject_text

    p1 = inject_text(tmp_path, "first")
    p2 = inject_text(tmp_path, "second")
    items = _drain_once(tmp_path)
    assert [t for _, t in items] == ["first", "second"]
    # ensure both paths returned (we'll later delete them)
    assert {p for p, _ in items} == {p1, p2}


def test_drain_once_skips_non_txt(tmp_path: Path):
    from src.inject import drain as _drain_once, inject_text

    inject_text(tmp_path, "real")
    (tmp_path / "stray.tmp").write_text("ignore me")
    (tmp_path / "stray.json").write_text("{}")

    items = _drain_once(tmp_path)
    assert [t for _, t in items] == ["real"]
