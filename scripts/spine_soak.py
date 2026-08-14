"""Endurance harness for the spine conductor.

Compresses "a week of living with it" into a measurable soak: hundreds of
turns run through the *real* SpineLoop (src/spine/loop.py), real
TurnAssembler (src/spine/turn.py), real EnergyVAD (src/spine/vad.py) and
real sentence splitter (src/spine/sentences.py) — only the network/device
edges are faked: STT, TTS and (offline, the default) the LLM. Persistence
(src/spine/persist.py) and usage accounting (src/spine/usage.py) run for
real against a throwaway sqlite db in a temp directory, because real disk
I/O is part of what a soak is supposed to catch.

    uv run python scripts/spine_soak.py --turns 200            # offline
    uv run python scripts/spine_soak.py --turns 20 --live      # real DeepSeek
    uv run python scripts/spine_soak.py --turns 50 --report json

The question a soak answers: does the conductor leak memory, file
descriptors or asyncio tasks, or does its turn latency degrade, over the
course of a long-running conversation? See soak() for the mechanics and
_evaluate() for the pass/fail criteria.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import resource
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

# Run either as `python scripts/spine_soak.py` (sys.path[0] == scripts/) or
# imported as `scripts.spine_soak` from a test at the repo root — either
# way the repo root must be importable so `from src...` below resolves.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.spine.loop import SpineLoop  # noqa: E402
from src.spine.sentences import sentences  # noqa: E402
from src.spine.turn import TurnAssembler  # noqa: E402
from src.spine.vad import EnergyVAD  # noqa: E402
from src.spine.persist import SpinePersistence  # noqa: E402
from src.spine.usage import SpineUsage  # noqa: E402


# -- synthetic speech ---------------------------------------------------

_RATE = 16000
_FRAME_MS = 20
_FRAME_BYTES = (_RATE * _FRAME_MS // 1000) * 2  # 640 bytes = 320 int16 samples
_SILENCE_FRAME = b"\x00" * _FRAME_BYTES


def _loud_frame(turn_idx: int) -> bytes:
    """A synthetic 20ms frame of "speech" well above EnergyVAD's -38dBFS
    gate. Varied by turn index (a shifting tone), not by randomness, so a
    run is reproducible frame-for-frame."""
    n = _FRAME_BYTES // 2
    amplitude = 6000
    freq = 180 + (turn_idx % 40) * 5
    samples = [
        int(amplitude * math.sin(2 * math.pi * freq * i / _RATE)) for i in range(n)
    ]
    return struct.pack(f"<{n}h", *samples)


def _utterance_frames(turn_idx: int, vad: EnergyVAD) -> list[bytes]:
    """One utterance's worth of frames: enough loud frames to cross
    start_frames (VAD onset), then enough silence to cross stop_frames
    (VAD offset) — the same shape a real utterance has."""
    onset = [_loud_frame(turn_idx)] * (vad.start_frames + 2)
    tail_silence = [_SILENCE_FRAME] * (vad.stop_frames + 1)
    return onset + tail_silence


# -- canned text, index-derived (no randomness) --------------------------

_UK_WORDS = [
    "Привіт", "як", "справи", "сьогодні", "гарна", "погода", "я", "думаю",
    "що", "все", "буде", "добре", "дякую", "за", "питання", "розкажи",
    "мені", "більше", "про", "це", "звичайно", "ось", "коротка", "відповідь",
    "яка", "має", "сенс", "і", "завершується", "тут",
]

_UK_PHRASES = [
    "привіт як справи",
    "яка сьогодні погода",
    "розкажи щось цікаве",
    "що нового",
    "допоможи мені будь ласка",
    "дякую за відповідь",
    "скільки зараз часу",
    "нагадай мені про справи",
]


def _offline_stream_chat_factory() -> Any:
    """Fake stream_chat: ~30 deltas of varied Ukrainian text per call,
    varied by an index-derived rotation through _UK_WORDS."""
    counter = {"n": 0}

    def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
        idx = counter["n"]
        counter["n"] += 1

        async def _gen() -> AsyncIterator[str]:
            start = (idx * 7) % len(_UK_WORDS)
            for i in range(30):
                word = _UK_WORDS[(start + i) % len(_UK_WORDS)]
                yield word + (". " if (i + 1) % 8 == 0 else " ")
                await asyncio.sleep(0)

        return _gen()

    return stream_chat


def _fake_transcribe_factory() -> Any:
    counter = {"n": 0}

    async def transcribe(pcm: bytes):
        idx = counter["n"]
        counter["n"] += 1

        class _Transcript:
            text = _UK_PHRASES[idx % len(_UK_PHRASES)]

        return _Transcript()

    return transcribe


async def _fake_synthesise(text: str) -> AsyncIterator[bytes]:
    """~50KB of PCM per sentence, in a handful of chunks."""
    chunk = bytes([0, 1]) * 5000  # 10_000 bytes
    for _ in range(5):
        yield chunk
        await asyncio.sleep(0)


class _SinkAudio:
    """FakeAudio-like sink: satisfies loop.py's AudioLike protocol without
    accumulating played bytes (a real device wouldn't either — accumulating
    them here would leak in the *harness*, muddying the very metrics the
    soak is trying to measure)."""

    def __init__(self) -> None:
        self.input_frames: asyncio.Queue = asyncio.Queue()
        self.mute_input = False
        self.play_count = 0
        self.bytes_played = 0

    def play(self, pcm: bytes) -> None:
        self.play_count += 1
        self.bytes_played += len(pcm)

    def stop_playback(self) -> int:
        dropped = self.bytes_played
        self.bytes_played = 0
        return dropped

    @property
    def playing(self) -> bool:
        return False  # queued audio "drains" instantly, as in test fakes


# -- concurrent reader (simulates the dashboard) -------------------------


class _ConcurrentReader:
    """A thread doing `SELECT COUNT(*) FROM transcripts` every `interval`
    seconds against the same db the conductor writes to — the dashboard's
    access pattern. Counts any sqlite error it sees; the soak wants zero."""

    def __init__(self, db_path: Path, interval: float = 0.2) -> None:
        self.db_path = db_path
        self.interval = interval
        self.errors = 0
        self.reads = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=1.0)
        except Exception:
            self.errors += 1
            conn = None
        while not self._stop.is_set():
            if conn is not None:
                try:
                    conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()
                    self.reads += 1
                except Exception:
                    self.errors += 1
            self._stop.wait(self.interval)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# -- metric collection ----------------------------------------------------


def _rss_maxrss_bytes() -> int:
    """resource.RUSAGE_SELF's high-water mark, normalised to bytes.
    ru_maxrss is bytes on Darwin, kilobytes on Linux."""
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru if sys.platform == "darwin" else ru * 1024


def _rss_current_bytes(pid: int) -> int:
    """The true *current* RSS (ru_maxrss only ever grows) via `ps`."""
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip()) * 1024  # ps reports 1K blocks


def _open_fds() -> int:
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return -1


def _db_size_bytes(db_path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            total += p.stat().st_size
    return total


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _sample(
    *, turn: int, pid: int, db_path: Path, latencies_ms: list[float], window: int
) -> dict:
    recent = latencies_ms[-window:] if latencies_ms else []
    return {
        "turn": turn,
        "rss_maxrss_bytes": _rss_maxrss_bytes(),
        "rss_current_bytes": _rss_current_bytes(pid),
        "fds": _open_fds(),
        "tasks": len(asyncio.all_tasks()),
        "db_size_bytes": _db_size_bytes(db_path),
        "p50_ms": _percentile(recent, 0.5),
        "p95_ms": _percentile(recent, 0.95),
    }


# -- verdicts --------------------------------------------------------------


def _evaluate(samples: list[dict], window: int, reader_errors: int) -> dict:
    """One bool + a human-readable detail per criterion. A criterion with
    too few samples to judge (short runs, e.g. the smoke test) reports
    ok=True with an "insufficient data" detail rather than failing."""
    verdicts: dict[str, dict] = {}

    baseline20 = next((s for s in samples if s["turn"] >= 20), None)
    final = samples[-1] if samples else None
    if baseline20 is not None and final is not None:
        before = max(baseline20["rss_current_bytes"], 1)
        growth = (final["rss_current_bytes"] - before) / before
        verdicts["rss_growth"] = {
            "ok": growth <= 0.25,
            "detail": (
                f"current RSS turn {baseline20['turn']}="
                f"{before / 1e6:.1f}MB -> turn {final['turn']}="
                f"{final['rss_current_bytes'] / 1e6:.1f}MB ({growth * 100:+.1f}%, limit +25%)"
            ),
        }
    else:
        verdicts["rss_growth"] = {
            "ok": True,
            "detail": "insufficient data (no sample at turn >= 20)",
        }

    if len(samples) >= 2:
        fd_values = [s["fds"] for s in samples]
        monotonic = all(b >= a for a, b in zip(fd_values, fd_values[1:]))
        fd_growth = fd_values[-1] - fd_values[0]
        verdicts["fd_growth"] = {
            "ok": not (monotonic and fd_growth > 20),
            "detail": (
                f"fds {fd_values[0]} -> {fd_values[-1]} "
                f"(monotonic={monotonic}, growth={fd_growth}, limit 20)"
            ),
        }
    else:
        verdicts["fd_growth"] = {"ok": True, "detail": "insufficient data"}

    baseline10 = next((s for s in samples if s["turn"] >= window), None)
    if baseline10 is not None and final is not None:
        limit = baseline10["tasks"] + 5
        verdicts["task_growth"] = {
            "ok": final["tasks"] <= limit,
            "detail": (
                f"tasks turn {baseline10['turn']}={baseline10['tasks']} -> "
                f"turn {final['turn']}={final['tasks']} (limit {limit})"
            ),
        }
    else:
        verdicts["task_growth"] = {"ok": True, "detail": "insufficient data"}

    first = samples[0] if samples else None
    if first is not None and final is not None:
        limit = 3 * max(first["p95_ms"], 1e-6)
        verdicts["latency_p95"] = {
            "ok": final["p95_ms"] <= limit,
            "detail": (
                f"p95 first window={first['p95_ms']:.1f}ms -> "
                f"last window={final['p95_ms']:.1f}ms (limit {limit:.1f}ms, 3x first)"
            ),
        }
    else:
        verdicts["latency_p95"] = {"ok": True, "detail": "insufficient data"}

    verdicts["reader_errors"] = {
        "ok": reader_errors == 0,
        "detail": f"{reader_errors} sqlite error(s) seen by the concurrent reader",
    }

    return verdicts


# -- the soak itself --------------------------------------------------------


async def soak(
    *,
    turns: int = 200,
    live: bool = False,
    window: int = 10,
) -> dict:
    """Run `turns` conversational turns through a real SpineLoop and
    return a metrics dict: {turns, live, window, duration_s, samples,
    reader_errors, verdicts, passed}. Importable and side-effect-free
    beyond a temp directory it creates and removes itself — safe to call
    from a test."""
    if live and turns > 50:
        turns = 50

    tmp_dir = Path(tempfile.mkdtemp(prefix="spine_soak_"))
    db_path = tmp_dir / "soak.db"
    reader = _ConcurrentReader(db_path)
    persist = SpinePersistence(db_path)
    usage = SpineUsage(db_path)
    reader.start()

    if live:
        from dotenv import load_dotenv

        load_dotenv(Path.home() / ".heare" / ".env", override=False)
        from src.config import load_settings
        from src.spine.llm import resolve_llm, stream_chat as _real_stream_chat

        settings = load_settings()
        try:
            cfg = resolve_llm(settings)
        except RuntimeError as exc:
            persist.close()
            usage.close()
            reader.stop()
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError(
                f"--live requires a DeepSeek API key (~/.heare/.env): {exc}"
            ) from exc

        def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
            return _real_stream_chat(messages, cfg)
    else:
        stream_chat = _offline_stream_chat_factory()

    audio = _SinkAudio()
    vad = EnergyVAD()
    hold_s = 0.05  # real clock, deliberately short so a soak run finishes
    assembler = TurnAssembler(hold_s=hold_s)
    transcribe = _fake_transcribe_factory()

    loop = SpineLoop(
        audio=audio,
        vad=vad,
        assembler=assembler,
        transcribe=transcribe,
        stream_chat=stream_chat,
        split_sentences=sentences,
        synthesise=_fake_synthesise,
        poll_interval=0.01,
        persist=persist,
        usage=usage,
    )

    latencies_ms: list[float] = []
    orig_respond = loop.respond

    async def _timed_respond(text: str, **kwargs: Any) -> str:
        t0 = time.perf_counter()
        result = await orig_respond(text, **kwargs)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        return result

    loop.respond = _timed_respond  # type: ignore[method-assign]

    run_task = asyncio.create_task(loop.run(), name="soak-spine-run")
    pid = os.getpid()
    samples: list[dict] = []
    per_turn_timeout = 30.0 if live else 5.0
    t_start = time.perf_counter()

    try:
        for turn_idx in range(turns):
            for frame in _utterance_frames(turn_idx, vad):
                audio.input_frames.put_nowait(frame)

            target = turn_idx + 1
            deadline = time.monotonic() + per_turn_timeout
            while len(latencies_ms) < target:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"turn {target} did not complete within "
                        f"{per_turn_timeout}s"
                    )
                await asyncio.sleep(0.005)

            turn_number = turn_idx + 1
            if turn_number % window == 0 or turn_number == turns:
                samples.append(
                    _sample(
                        turn=turn_number,
                        pid=pid,
                        db_path=db_path,
                        latencies_ms=latencies_ms,
                        window=window,
                    )
                )
    finally:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        reader.stop()
        persist.close()
        usage.close()
        # Capture db size *before* the directory disappears, in case a
        # caller wants it — it's already folded into each sample above.
        shutil.rmtree(tmp_dir, ignore_errors=True)

    duration_s = time.perf_counter() - t_start
    verdicts = _evaluate(samples, window, reader.errors)
    passed = all(v["ok"] for v in verdicts.values())

    return {
        "turns": turns,
        "live": live,
        "window": window,
        "duration_s": duration_s,
        "samples": samples,
        "reader_reads": reader.reads,
        "reader_errors": reader.errors,
        "verdicts": verdicts,
        "passed": passed,
    }


# -- reporting ---------------------------------------------------------------


def _print_table(metrics: dict) -> None:
    headers = [
        "turn", "rss_maxrss_mb", "rss_cur_mb", "fds", "tasks",
        "p50_ms", "p95_ms", "db_kb",
    ]
    print(" ".join(f"{h:>13}" for h in headers))
    for s in metrics["samples"]:
        row = [
            s["turn"],
            f"{s['rss_maxrss_bytes'] / 1e6:.1f}",
            f"{s['rss_current_bytes'] / 1e6:.1f}",
            s["fds"],
            s["tasks"],
            f"{s['p50_ms']:.1f}",
            f"{s['p95_ms']:.1f}",
            f"{s['db_size_bytes'] / 1024:.1f}",
        ]
        print(" ".join(f"{str(v):>13}" for v in row))
    print()
    for name, v in metrics["verdicts"].items():
        status = "PASS" if v["ok"] else "FAIL"
        print(f"{status}  {name}: {v['detail']}")
    print(
        f"\nreader: {metrics['reader_reads']} reads, "
        f"{metrics['reader_errors']} errors"
    )
    mode = "live" if metrics["live"] else "offline"
    overall = "PASS" if metrics["passed"] else "FAIL"
    print(
        f"\n{overall} — soak of {metrics['turns']} turns ({mode}) "
        f"in {metrics['duration_s']:.1f}s"
    )


async def _amain(args: argparse.Namespace) -> dict:
    return await soak(turns=args.turns, live=args.live, window=args.window)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Endurance soak for the spine conductor (src/spine)"
    )
    parser.add_argument("--turns", type=int, default=200, help="number of turns")
    parser.add_argument(
        "--live", action="store_true",
        help="use real DeepSeek for stream_chat (capped at 50 turns); TTS stays fake",
    )
    parser.add_argument(
        "--report", choices=["table", "json"], default="table",
        help="output format",
    )
    parser.add_argument(
        "--window", type=int, default=10,
        help="sample metrics every N turns",
    )
    args = parser.parse_args()

    try:
        metrics = asyncio.run(_amain(args))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.report == "json":
        print(json.dumps(metrics, indent=2))
    else:
        _print_table(metrics)
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
