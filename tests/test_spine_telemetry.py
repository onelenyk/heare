"""Telemetry — the per-turn instrument, tested without a running spine.

Everything here drives ``Telemetry`` directly: no loop, no audio, no
daemon. The wiring in ``src/daemon/spine_engine.py`` is exercised by the
existing spine integration tests exactly as before — this file is about
the instrument itself: one correct line per turn, no exception ever
escapes it, and a junk-dropped turn still shows up in the aggregate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.spine import telemetry as telemetry_mod
from src.spine.telemetry import Telemetry, TurnMetrics


def _fake_clock(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> None:
    """Replace time.monotonic() with a fixed, ordered sequence of values."""
    it = iter(values)
    monkeypatch.setattr(telemetry_mod.time, "monotonic", lambda: next(it))


def _fake_wall_clock(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(telemetry_mod.time, "time", lambda: value)


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


# -- one correct JSONL line, in order --------------------------------------


def test_in_order_calls_produce_one_correct_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "turns.jsonl"
    _fake_wall_clock(monkeypatch, 1700000000.0)
    # turn_closed -> t0=100.0, first_delta -> 100.05 (think=50ms),
    # first_audio -> 100.12 (speak=70ms, total=120ms). The 4th value is
    # finish()'s own "now" read, unused here since first_audio() fired.
    _fake_clock(monkeypatch, [100.0, 100.05, 100.12, 999.0])

    t = Telemetry(path)
    t.stt(42, dropped=False)
    t.turn_closed()
    t.first_delta()
    t.first_audio()
    t.finish(chars=10, interrupted=False, role="teacher")

    lines = _read_lines(path)
    assert len(lines) == 1
    row = lines[0]
    assert row == {
        "ts": 1700000000.0,
        "stt_ms": 42,
        "think_ms": 50,
        "speak_ms": 70,
        "total_ms": 120,
        "chars": 10,
        "interrupted": False,
        "dropped_junk": False,
        "role": "teacher",
    }
    # Round-trips through the dataclass shape too.
    assert TurnMetrics(**row) == TurnMetrics(
        ts=1700000000.0,
        stt_ms=42,
        think_ms=50,
        speak_ms=70,
        total_ms=120,
        chars=10,
        interrupted=False,
        dropped_junk=False,
        role="teacher",
    )


def test_finish_resets_for_the_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "turns.jsonl"
    _fake_wall_clock(monkeypatch, 1.0)
    _fake_clock(
        monkeypatch,
        # turn 1: t0, delta, audio, finish's own "now" (unused, t_audio set)
        [100.0, 100.01, 100.02, 100.5]
        # turn 2: t0 only (no delta/audio this time) + finish's now
        + [200.0, 200.03],
    )

    t = Telemetry(path)
    t.stt(5, dropped=False)
    t.turn_closed()
    t.first_delta()
    t.first_audio()
    t.finish(chars=1, interrupted=False, role="")

    # A second turn with no stt() call and no first_delta/first_audio —
    # must not carry over the previous turn's numbers.
    t.turn_closed()
    t.finish(chars=2, interrupted=True, role="")

    lines = _read_lines(path)
    assert len(lines) == 2
    second = lines[1]
    assert second["stt_ms"] == 0
    assert second["think_ms"] == 0
    assert second["speak_ms"] == 0
    assert second["total_ms"] == 30  # 200.03 - 200.0, in ms
    assert second["chars"] == 2
    assert second["interrupted"] is True


# -- out-of-order / missing calls: never raise, sane defaults --------------


def test_finish_with_nothing_called_first_does_not_raise(tmp_path: Path) -> None:
    t = Telemetry(tmp_path / "turns.jsonl")
    t.finish(chars=0, interrupted=False, role="")  # no stt/turn_closed/etc at all

    rows = _read_lines(tmp_path / "turns.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["stt_ms"] == 0
    assert row["think_ms"] == 0
    assert row["speak_ms"] == 0
    assert row["total_ms"] == 0
    assert row["dropped_junk"] is False
    assert row["role"] == ""


def test_first_delta_before_turn_closed_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    t = Telemetry(path)
    t.first_delta()  # stray — no turn open yet
    t.first_audio()  # stray too
    t.turn_closed()
    t.finish(chars=0, interrupted=False, role="")

    row = _read_lines(path)[0]
    assert row["think_ms"] == 0
    assert row["speak_ms"] == 0


def test_first_delta_only_the_first_call_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "turns.jsonl"
    _fake_wall_clock(monkeypatch, 1.0)
    # t0, first first_delta (used) — the second first_delta() early-returns
    # before touching the clock at all, so it consumes no value; finish()
    # still reads its own "now" last.
    _fake_clock(monkeypatch, [100.0, 100.05, 999.0])

    t = Telemetry(path)
    t.turn_closed()
    t.first_delta()
    t.first_delta()  # would blow up think_ms if not idempotent
    t.finish(chars=0, interrupted=False, role="")

    row = _read_lines(path)[0]
    assert row["think_ms"] == 50


def test_repeated_finish_without_new_turn_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    t = Telemetry(path)
    t.finish(chars=1, interrupted=False, role="")
    t.finish(chars=2, interrupted=False, role="")  # nothing in flight — still fine

    rows = _read_lines(path)
    assert len(rows) == 2
    assert all(r["total_ms"] == 0 for r in rows)


# -- write failure is swallowed ---------------------------------------------


def test_unwritable_path_is_swallowed(tmp_path: Path) -> None:
    # Parent is a plain file, not a directory: mkdir(parents=True) and
    # open("a") both fail, regardless of who runs the test.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    path = blocker / "turns.jsonl"

    t = Telemetry(path)
    t.turn_closed()
    t.finish(chars=3, interrupted=False, role="x")  # must not raise

    assert not path.exists()
    # The instrument still resets even when the write failed, so the
    # next turn starts clean instead of inheriting stale timers.
    t.turn_closed()
    assert t._t_delta is None


# -- junk-dropped turns: counted, no think/speak timings --------------------


def test_dropped_junk_turn_has_no_think_or_speak_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors the real wiring: an empty STT result calls finish() right
    from the stt() call, without turn_closed()/first_delta()/first_audio()
    ever running — there is no turn to time."""
    path = tmp_path / "turns.jsonl"
    _fake_wall_clock(monkeypatch, 5.0)

    t = Telemetry(path)
    t.stt(30, dropped=True)
    t.finish(chars=0, interrupted=False, role="")

    row = _read_lines(path)[0]
    assert row["dropped_junk"] is True
    assert row["stt_ms"] == 30
    assert row["think_ms"] == 0
    assert row["speak_ms"] == 0
    assert row["total_ms"] == 0


# -- summary(): aggregate over a synthetic file ------------------------------


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_summary_computes_percentiles_and_rates(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    timed = [100, 200, 300, 400, 500]
    stt_values = [10, 20, 30, 40, 50, 5, 7]
    rows = [
        {
            "ts": 1.0,
            "stt_ms": stt_values[i],
            "think_ms": 10,
            "speak_ms": 10,
            "total_ms": total,
            "chars": 5,
            "interrupted": i == 0,
            "dropped_junk": False,
            "role": "",
        }
        for i, total in enumerate(timed)
    ] + [
        {
            "ts": 1.0,
            "stt_ms": stt_values[5],
            "think_ms": 0,
            "speak_ms": 0,
            "total_ms": 0,
            "chars": 0,
            "interrupted": False,
            "dropped_junk": True,
            "role": "",
        },
        {
            "ts": 1.0,
            "stt_ms": stt_values[6],
            "think_ms": 0,
            "speak_ms": 0,
            "total_ms": 0,
            "chars": 0,
            "interrupted": True,
            "dropped_junk": True,
            "role": "",
        },
    ]
    _write_rows(path, rows)

    t = Telemetry(path)
    result = t.summary()

    assert result["count"] == 7
    # p50: exact middle of [100,200,300,400,500] -> 300
    assert result["p50_total_ms"] == 300
    # p90: linear interpolation between 400 and 500 at k=3.6 -> 460
    assert result["p90_total_ms"] == 460
    assert result["mean_stt_ms"] == pytest.approx(sum(stt_values) / 7)
    assert result["junk_rate"] == pytest.approx(2 / 7)
    assert result["interrupt_rate"] == pytest.approx(2 / 7)


def test_summary_on_empty_file_is_all_zero(tmp_path: Path) -> None:
    t = Telemetry(tmp_path / "missing.jsonl")
    result = t.summary()
    assert result == {
        "count": 0,
        "p50_total_ms": 0,
        "p90_total_ms": 0,
        "mean_stt_ms": 0.0,
        "junk_rate": 0.0,
        "interrupt_rate": 0.0,
    }


def test_summary_since_ts_filters_older_rows(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    rows = [
        {
            "ts": ts,
            "stt_ms": 1,
            "think_ms": 1,
            "speak_ms": 1,
            "total_ms": 100,
            "chars": 1,
            "interrupted": False,
            "dropped_junk": False,
            "role": "",
        }
        for ts in (1.0, 2.0, 3.0)
    ]
    _write_rows(path, rows)

    t = Telemetry(path)
    assert t.summary()["count"] == 3
    assert t.summary(since_ts=2.5)["count"] == 1


def test_summary_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    path.write_text(
        '{"ts": 1.0, "stt_ms": 1, "think_ms": 1, "speak_ms": 1, "total_ms": 100, '
        '"chars": 1, "interrupted": false, "dropped_junk": false, "role": ""}\n'
        "not json at all\n"
        "\n",
        encoding="utf-8",
    )
    t = Telemetry(path)
    assert t.summary()["count"] == 1
