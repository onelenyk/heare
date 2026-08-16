"""Cheap per-turn quality telemetry — one JSON line per turn.

Before this, "it interrupts me too early" or "replies got slow" could only
be answered by hand-grepping the daemon log. This module is the missing
instrument: every turn writes one line to ``~/.heare/logs/turns.jsonl``
with how long each stage took, so the question can be answered by reading
a file instead of listening again.

Stdlib only — this file must import cleanly with no pipecat, no audio
libraries, nothing that could make ``import src.daemon.spine_engine`` slow
or fragile. It never raises into the conversation: every public method
swallows its own errors and logs them at debug level, because a broken
instrument must never be the reason a turn fails.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("heare.spine.telemetry")


@dataclass
class TurnMetrics:
    """One turn, one line. Field order here is the field order on disk."""

    ts: float
    stt_ms: int  # transcription round trip
    think_ms: int  # first LLM delta after the turn closed
    speak_ms: int  # first audio chunk after that
    total_ms: int  # turn close -> first audio
    chars: int  # reply length
    interrupted: bool
    dropped_junk: bool  # the hallucination filter ate this utterance
    role: str  # active role name or ""


def _percentile(sorted_values: list[int], pct: float) -> int:
    """Linear-interpolation percentile over an already-sorted list."""
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return int(sorted_values[0])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return int(sorted_values[int(k)])
    lo_val = sorted_values[int(lo)] * (hi - k)
    hi_val = sorted_values[int(hi)] * (k - lo)
    return int(round(lo_val + hi_val))


class Telemetry:
    """One line per turn, appended to a JSONL file.

    The spine answers one turn at a time (``_converse`` is sequential), so
    a single in-flight turn's timers live on the instance. Calls that
    arrive out of order or are simply missing (no STT for an injected
    turn, no audio for a role's silent channel) must not raise — they are
    ignored and the written line falls back to sane defaults (0 / False).

    Never raises into the conversation; a failed write is a debug log.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._reset()

    def _reset(self) -> None:
        self._stt_ms: int = 0
        self._dropped_junk: bool = False
        self._t0: float | None = None  # turn_closed() — monotonic
        self._t_delta: float | None = None  # first_delta() — monotonic
        self._t_audio: float | None = None  # first_audio() — monotonic

    def stt(self, ms: int, *, dropped: bool) -> None:
        """Record one transcription round trip.

        ``dropped`` marks an utterance that produced no text — the cheap
        proxy for "the hallucination filter ate this". Overwrites any
        earlier reading for this in-flight turn (multi-utterance turns
        keep only the latest STT timing — cheap telemetry, not exact).
        """
        try:
            self._stt_ms = max(0, int(ms))
            self._dropped_junk = bool(dropped)
        except Exception:
            logger.debug("telemetry.stt failed (non-fatal)", exc_info=True)

    def turn_closed(self) -> None:
        """Mark the start of a turn: t0 for think_ms/total_ms."""
        try:
            self._t0 = time.monotonic()
            self._t_delta = None
            self._t_audio = None
        except Exception:
            logger.debug("telemetry.turn_closed failed (non-fatal)", exc_info=True)

    def first_delta(self) -> None:
        """Mark the first LLM delta. Ignored if no turn is open, or if
        already marked (only the first call counts)."""
        try:
            if self._t0 is None or self._t_delta is not None:
                return
            self._t_delta = time.monotonic()
        except Exception:
            logger.debug("telemetry.first_delta failed (non-fatal)", exc_info=True)

    def first_audio(self) -> None:
        """Mark the first audio chunk. Same first-wins, no-turn-no-op
        rule as first_delta()."""
        try:
            if self._t0 is None or self._t_audio is not None:
                return
            self._t_audio = time.monotonic()
        except Exception:
            logger.debug("telemetry.first_audio failed (non-fatal)", exc_info=True)

    def finish(self, *, chars: int, interrupted: bool, role: str) -> None:
        """Write the line for the in-flight turn, then reset.

        Missing timers (no turn_closed(), no first_delta(), no
        first_audio()) fall back to 0 rather than raising — a
        junk-dropped turn has no think/speak timings by design."""
        try:
            now = time.monotonic()
            t0 = self._t0
            think_ms = 0
            speak_ms = 0
            total_ms = 0
            if t0 is not None:
                if self._t_delta is not None:
                    # round(), not int(): truncating toward zero would
                    # systematically shave up to 1ms off every reading —
                    # a small but consistent lie in a timing instrument.
                    think_ms = round((self._t_delta - t0) * 1000)
                    if self._t_audio is not None:
                        speak_ms = round((self._t_audio - self._t_delta) * 1000)
                total_ms = round(
                    ((self._t_audio if self._t_audio is not None else now) - t0)
                    * 1000
                )
            metrics = TurnMetrics(
                ts=time.time(),
                stt_ms=self._stt_ms,
                think_ms=max(0, think_ms),
                speak_ms=max(0, speak_ms),
                total_ms=max(0, total_ms),
                chars=max(0, int(chars)),
                interrupted=bool(interrupted),
                dropped_junk=self._dropped_junk,
                role=role or "",
            )
            self._write(metrics)
        except Exception:
            logger.debug("telemetry.finish failed (non-fatal)", exc_info=True)
        finally:
            self._reset()

    def _write(self, metrics: TurnMetrics) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(metrics), ensure_ascii=False)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except Exception:
            logger.debug("telemetry write failed (non-fatal)", exc_info=True)

    def summary(self, since_ts: float | None = None) -> dict:
        """Aggregate the file: count, p50/p90 total_ms, mean stt_ms,
        junk rate, interrupt rate — for a future dashboard card.

        Percentiles are computed only over turns that actually completed
        (not junk-dropped, which carry no total_ms by design); junk_rate
        and interrupt_rate are over every turn."""
        empty = {
            "count": 0,
            "p50_total_ms": 0,
            "p90_total_ms": 0,
            "mean_stt_ms": 0.0,
            "junk_rate": 0.0,
            "interrupt_rate": 0.0,
        }
        try:
            rows = self._read_rows(since_ts)
        except Exception:
            logger.debug("telemetry summary failed (non-fatal)", exc_info=True)
            return empty

        count = len(rows)
        if count == 0:
            return empty

        timed = sorted(
            int(r.get("total_ms", 0)) for r in rows if not r.get("dropped_junk")
        )
        stt_values = [int(r.get("stt_ms", 0)) for r in rows]
        junk = sum(1 for r in rows if r.get("dropped_junk"))
        interrupted = sum(1 for r in rows if r.get("interrupted"))

        return {
            "count": count,
            "p50_total_ms": _percentile(timed, 50),
            "p90_total_ms": _percentile(timed, 90),
            "mean_stt_ms": (sum(stt_values) / count) if count else 0.0,
            "junk_rate": junk / count,
            "interrupt_rate": interrupted / count,
        }

    def _read_rows(self, since_ts: float | None) -> list[dict]:
        rows: list[dict] = []
        if not self._path.exists():
            return rows
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if since_ts is not None and row.get("ts", 0) < since_ts:
                    continue
                rows.append(row)
        return rows


__all__ = ["Telemetry", "TurnMetrics"]
