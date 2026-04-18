#!/usr/bin/env python3
"""Benchmark `claude -p` streaming TTFT for US-P1-00.

Usage:
    uv run python scripts/bench_claude_ttft.py

Measures:
    - time_to_first_stream_event (ms)
    - total_wall_time (ms)
    - models: haiku, sonnet
    - conditions: 5x cold (with gap), 5x warm (consecutive)
"""
from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import time
from pathlib import Path

PROMPT = "коротко скажи привіт"
COLD_GAP_SECS = 30
MODELS = ["haiku", "sonnet"]
RUNS_PER_MODE = 5


async def single_run(model: str) -> tuple[float, float]:
    """Return (ttft_ms, total_ms)."""
    args = [
        "claude",
        "-p",
        PROMPT,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    ttft: float | None = None
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        # first line with a text event = TTFT
        if ttft is None:
            try:
                event = json.loads(line)
                # look for any assistant-text event
                s = json.dumps(event)
                if '"type": "text"' in s or '"type":"text"' in s or event.get("type") == "assistant":
                    ttft = (time.monotonic() - start) * 1000
            except json.JSONDecodeError:
                pass
    await proc.wait()
    total = (time.monotonic() - start) * 1000
    if ttft is None:
        ttft = total  # fallback: no text event detected
    return ttft, total


async def bench_model(model: str) -> dict:
    cold_ttfts, cold_totals = [], []
    warm_ttfts, warm_totals = [], []

    # Cold runs: gap between each
    for i in range(RUNS_PER_MODE):
        if i > 0:
            await asyncio.sleep(COLD_GAP_SECS)
        print(f"  cold run {i+1}/{RUNS_PER_MODE}...", flush=True)
        ttft, total = await single_run(model)
        cold_ttfts.append(ttft)
        cold_totals.append(total)
        print(f"    ttft={ttft:.0f}ms total={total:.0f}ms", flush=True)

    # Warm runs: back-to-back
    print(f"  warm phase (back-to-back)...", flush=True)
    for i in range(RUNS_PER_MODE):
        ttft, total = await single_run(model)
        warm_ttfts.append(ttft)
        warm_totals.append(total)
        print(f"    warm {i+1}/{RUNS_PER_MODE}: ttft={ttft:.0f}ms total={total:.0f}ms", flush=True)

    return {
        "model": model,
        "cold_ttfts_ms": cold_ttfts,
        "cold_totals_ms": cold_totals,
        "warm_ttfts_ms": warm_ttfts,
        "warm_totals_ms": warm_totals,
        "cold_ttft_median_ms": statistics.median(cold_ttfts),
        "warm_ttft_median_ms": statistics.median(warm_ttfts),
        "cold_total_median_ms": statistics.median(cold_totals),
        "warm_total_median_ms": statistics.median(warm_totals),
    }


async def main() -> None:
    results = []
    for model in MODELS:
        print(f"\n=== model={model} ===", flush=True)
        results.append(await bench_model(model))

    out = Path(".omc/benchmarks/phase1-latency-baseline.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Phase 1 — `claude -p` streaming TTFT baseline",
        "",
        f"Prompt: `{PROMPT}`",
        f"Flags: `-p --model <m> --output-format stream-json --verbose --dangerously-skip-permissions`",
        f"Runs per mode: cold={RUNS_PER_MODE} (gap={COLD_GAP_SECS}s), warm={RUNS_PER_MODE} (back-to-back)",
        "",
        "## Results",
        "",
        "| model | cold TTFT median | warm TTFT median | cold total median | warm total median |",
        "|-------|-----------------|------------------|-------------------|-------------------|",
    ]
    for r in results:
        lines.append(
            f"| {r['model']} | {r['cold_ttft_median_ms']:.0f} ms | "
            f"{r['warm_ttft_median_ms']:.0f} ms | "
            f"{r['cold_total_median_ms']:.0f} ms | "
            f"{r['warm_total_median_ms']:.0f} ms |"
        )

    lines += ["", "## Raw runs", ""]
    for r in results:
        lines.append(f"### {r['model']}")
        lines.append("")
        lines.append(f"Cold ttfts (ms): {r['cold_ttfts_ms']}")
        lines.append(f"Cold totals (ms): {r['cold_totals_ms']}")
        lines.append(f"Warm ttfts (ms): {r['warm_ttfts_ms']}")
        lines.append(f"Warm totals (ms): {r['warm_totals_ms']}")
        lines.append("")

    # Verdict
    haiku = next((r for r in results if r["model"] == "haiku"), None)
    verdict = "UNKNOWN"
    reason = ""
    if haiku:
        warm_ok = haiku["warm_ttft_median_ms"] <= 1500
        cold_ok = haiku["cold_ttft_median_ms"] <= 3500
        if warm_ok and cold_ok:
            verdict = "GO"
            reason = f"haiku warm TTFT {haiku['warm_ttft_median_ms']:.0f}ms <= 1500ms AND cold TTFT {haiku['cold_ttft_median_ms']:.0f}ms <= 3500ms"
        else:
            verdict = "NO-GO"
            reason = f"haiku warm {haiku['warm_ttft_median_ms']:.0f}ms (need <=1500ms) / cold {haiku['cold_ttft_median_ms']:.0f}ms (need <=3500ms)"

    lines += [
        "## Verdict",
        "",
        f"**{verdict}** — {reason}",
        "",
    ]

    out.write_text("\n".join(lines))
    print(f"\n{'='*60}\nVERDICT: {verdict}\n{reason}\n\nWritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
