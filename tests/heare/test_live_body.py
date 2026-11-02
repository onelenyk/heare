"""`make live` on a body: the parts must be new, up and not falling."""

from __future__ import annotations

import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "scripts" / "live.py"
spec = importlib.util.spec_from_file_location("live", path)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
WANTED = {"voice": "voice"}


def test_a_running_new_part_is_steady():
    assert live.parts_steady({"voice": {"state": "running", "at": 10, "restarts": 0}}, WANTED, 5)


def test_an_old_or_falling_or_missing_part_is_not():
    assert not live.parts_steady({"voice": {"state": "running", "at": 1}}, WANTED, 5)
    assert not live.parts_steady({"voice": {"state": "running", "at": 10, "restarts": 1}}, WANTED, 5)
    assert not live.parts_steady({}, WANTED, 5)
