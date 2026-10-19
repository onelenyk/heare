"""A child of the app goes when the app is gone."""

from __future__ import annotations

import os
import threading

from heare import lifeline


def test_nothing_is_watched_without_an_app(monkeypatch):
    monkeypatch.delenv("HEARE_PARENT_PID", raising=False)
    assert lifeline.watch(lambda: None) is False


def test_a_child_leaves_when_its_app_is_gone(monkeypatch):
    monkeypatch.setenv("HEARE_PARENT_PID", str(os.getppid()))
    parents = iter([os.getppid(), os.getppid(), 1])
    monkeypatch.setattr(lifeline.os, "getppid", lambda: next(parents, 1))
    gone = threading.Event()

    assert lifeline.watch(gone.set, every=0.01) is True
    assert gone.wait(2)
