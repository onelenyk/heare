"""The weekly digest: what the journal already knows about irritations."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from scripts.digest import Signal, _counts, _per_day, _signals


def _event(kind: str, payload: dict, at: str = "2026-10-27T09:14:02.000+02:00") -> str:
    return json.dumps({
        "id": "ev_1",
        "at": at,
        "kind": kind,
        "source": "core",
        "trust": "system",
        "trace_id": "",
        "payload": payload,
    })


def _journal(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_signals_keep_the_seven_kinds_and_drop_the_noise(tmp_path):
    _journal(
        tmp_path / "journal.jsonl",
        _event("halted", {"tasks": 2}),
        _event("voice_stopped", {}),
        _event("failed", {"channel": "voice"}),
        _event("cannot", {"what": "set_volume", "why": "no such verb"}),
        _event("question_closed", {}),
        _event("feature", {"feature": "eyes"}),
        _event("task", {"status": "failed", "executor": "hands"}),
        _event("heard", {"text": "hello"}),
        _event("said", {"text": "hi"}),
        _event("verb", {"name": "set_volume"}),
        _event("permission", {"part": "hands"}),
        _event("judged", {"action": "run"}),
        _event("around", {"app": "Safari"}),
        _event("task", {"status": "done"}),
    )

    signals = _signals(tmp_path, 7)

    assert [signal.kind for signal in signals] == [
        "halted",
        "voice_stopped",
        "failed",
        "cannot",
        "question_closed",
        "feature",
        "task failed",
    ]


def test_a_cannot_carries_the_reason_and_the_verb(tmp_path):
    _journal(
        tmp_path / "journal.jsonl",
        _event("cannot", {"what": "set_volume", "why": "no such verb"}),
    )

    [signal] = _signals(tmp_path, 7)

    assert "no such verb" in signal.detail
    assert "set_volume" in signal.detail


def test_a_stopped_task_is_included_too(tmp_path):
    _journal(
        tmp_path / "journal.jsonl",
        _event("task", {"status": "stopped", "request": "build the thing"}),
    )

    [signal] = _signals(tmp_path, 7)

    assert signal.kind == "task stopped"
    assert signal.detail == "build the thing"


def test_counts_order_by_count_then_name():
    signals = [
        Signal("2026-10-27T09:00:00", "2026-10-27", "failed", ""),
        Signal("2026-10-27T09:01:00", "2026-10-27", "cannot", ""),
        Signal("2026-10-27T09:02:00", "2026-10-27", "cannot", ""),
        Signal("2026-10-27T09:03:00", "2026-10-27", "halted", ""),
        Signal("2026-10-27T09:04:00", "2026-10-27", "voice_stopped", ""),
        Signal("2026-10-28T09:05:00", "2026-10-28", "cannot", ""),
        Signal("2026-10-28T09:06:00", "2026-10-28", "failed", ""),
    ]

    assert _counts(signals) == [
        ("cannot", 3),
        ("failed", 2),
        ("halted", 1),
        ("voice_stopped", 1),
    ]


def test_per_day_counts_only_days_that_happened():
    signals = [
        Signal("2026-10-27T09:00:00", "2026-10-27", "failed", ""),
        Signal("2026-10-29T09:01:00", "2026-10-29", "cannot", ""),
        Signal("2026-10-29T09:02:00", "2026-10-29", "cannot", ""),
    ]

    assert _per_day(signals) == [("2026-10-27", 1), ("2026-10-29", 2)]


def test_an_empty_directory_has_nothing(tmp_path):
    assert _signals(tmp_path, 7) == []


def test_broken_json_is_skipped_not_raised(tmp_path):
    _journal(
        tmp_path / "journal.jsonl",
        _event("halted", {"tasks": 1}),
        "{not json",
        _event("cannot", {"what": "set_volume", "why": "no such verb"}),
    )

    assert len(_signals(tmp_path, 7)) == 2


def test_a_rejected_fact_is_a_signal_and_a_confirmed_one_is_not(tmp_path):
    _journal(
        tmp_path / "journal.jsonl",
        _event("mind", {"what": "fact", "action": "rejected", "id": 10}),
        _event("mind", {"what": "fact", "action": "confirmed", "id": 9}),
    )

    signals = _signals(tmp_path, 7)

    assert len(signals) == 1
    assert signals[0].kind == "fact rejected"
    assert signals[0].detail == "#10"


def test_files_older_than_the_cutoff_are_not_read(tmp_path):
    path = _journal(
        tmp_path / "journal.jsonl",
        _event("halted", {"tasks": 1}),
    )
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))

    assert _signals(tmp_path, 7) == []
