"""Smoke test for the spine soak harness (scripts/spine_soak.py).

Not a soak in itself — 15 turns, offline, just enough to prove the
harness's plumbing works: it drives a real SpineLoop/TurnAssembler/
EnergyVAD to completion, samples metrics, and produces a verdict. A real
endurance run is `uv run python scripts/spine_soak.py --turns 200`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.spine_soak import soak  # noqa: E402


async def test_soak_smoke_passes_with_expected_metrics() -> None:
    metrics = await soak(turns=15, live=False, window=10)

    assert metrics["passed"] is True, metrics["verdicts"]
    assert metrics["turns"] == 15
    assert metrics["live"] is False
    assert metrics["reader_errors"] == 0

    # Samples at turn 10 and the final turn (15) — window=10, plus a
    # forced sample on the last turn even when it isn't a multiple of it.
    assert [s["turn"] for s in metrics["samples"]] == [10, 15]

    expected_sample_keys = {
        "turn", "rss_maxrss_bytes", "rss_current_bytes", "fds", "tasks",
        "db_size_bytes", "p50_ms", "p95_ms",
    }
    for sample in metrics["samples"]:
        assert expected_sample_keys <= sample.keys()
        assert sample["rss_maxrss_bytes"] > 0
        assert sample["rss_current_bytes"] > 0
        assert sample["fds"] > 0
        assert sample["tasks"] > 0
        assert sample["db_size_bytes"] > 0

    expected_verdict_names = {
        "rss_growth", "fd_growth", "task_growth", "latency_p95", "reader_errors",
    }
    assert expected_verdict_names == metrics["verdicts"].keys()
    for verdict in metrics["verdicts"].values():
        assert verdict["ok"] is True
        assert isinstance(verdict["detail"], str) and verdict["detail"]
