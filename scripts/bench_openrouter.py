#!/usr/bin/env python3
"""Benchmark OpenRouter streaming TTFT for Phase 1 pivot.

Sends 10 sequential prompts to the chosen model via SSE streaming.
Measures time-to-first-token (TTFT) and total wall time per request.
Writes verdict to .omc/benchmarks/phase1-openrouter.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

MODEL = "google/gemini-3.1-flash-lite-preview-20260303"
PROMPT = "Коротко скажи привіт українською. Одне речення."
NUM_REQUESTS = 10
URL = "https://openrouter.ai/api/v1/chat/completions"


async def single_request(client: httpx.AsyncClient, api_key: str) -> tuple[float, float, str]:
    """Return (ttft_ms, total_ms, final_text)."""
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 60,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/onelenyk/heare",
        "X-Title": "heare-phase1-bench",
    }
    start = time.monotonic()
    ttft: float | None = None
    collected: list[str] = []
    async with client.stream("POST", URL, json=payload, headers=headers) as resp:
        if resp.status_code != 200:
            body = await resp.aread()
            raise RuntimeError(f"HTTP {resp.status_code}: {body.decode()[:500]}")
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = event.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content")
            if content:
                if ttft is None:
                    ttft = (time.monotonic() - start) * 1000
                collected.append(content)
    total = (time.monotonic() - start) * 1000
    if ttft is None:
        ttft = total
    return ttft, total, "".join(collected).strip()


async def main() -> None:
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY missing from .env")

    ttfts: list[float] = []
    totals: list[float] = []
    last_text = ""
    print(f"Model: {MODEL}")
    print(f"Sending {NUM_REQUESTS} sequential streaming requests...\n")
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(NUM_REQUESTS):
            try:
                ttft, total, text = await single_request(client, api_key)
                ttfts.append(ttft)
                totals.append(total)
                last_text = text
                print(f"  {i+1}/{NUM_REQUESTS}: ttft={ttft:.0f}ms total={total:.0f}ms")
            except Exception as e:
                print(f"  {i+1}/{NUM_REQUESTS}: FAILED — {e}")

    if not ttfts:
        raise SystemExit("all requests failed — see errors above")

    out = Path(".omc/benchmarks/phase1-openrouter.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    ttft_med = statistics.median(ttfts)
    total_med = statistics.median(totals)
    ttft_warm_med = statistics.median(ttfts[1:]) if len(ttfts) > 1 else ttft_med

    if ttft_warm_med <= 1000:
        verdict = "GO"
        reason = f"warm TTFT median {ttft_warm_med:.0f}ms ≤ 1000ms target"
    elif ttft_warm_med <= 1500:
        verdict = "GO (marginal)"
        reason = f"warm TTFT median {ttft_warm_med:.0f}ms — within 1.5s tolerance"
    else:
        verdict = "NO-GO"
        reason = f"warm TTFT median {ttft_warm_med:.0f}ms > 1500ms"

    lines = [
        "# Phase 1 — OpenRouter streaming TTFT benchmark",
        "",
        f"Model: `{MODEL}`",
        f"Prompt: `{PROMPT}`",
        f"Requests: {NUM_REQUESTS} sequential",
        "",
        "## Results",
        "",
        f"- First TTFT: {ttfts[0]:.0f} ms",
        f"- Warm TTFT median (runs 2+): {ttft_warm_med:.0f} ms",
        f"- All TTFT median: {ttft_med:.0f} ms",
        f"- Total median: {total_med:.0f} ms",
        "",
        f"Raw TTFTs (ms): `{[round(t) for t in ttfts]}`",
        f"Raw totals (ms): `{[round(t) for t in totals]}`",
        "",
        f"Sample reply: `{last_text}`",
        "",
        "## Verdict",
        "",
        f"**{verdict}** — {reason}",
        "",
    ]
    out.write_text("\n".join(lines))
    print(f"\n{'='*60}\nVERDICT: {verdict}\n{reason}\nWritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
