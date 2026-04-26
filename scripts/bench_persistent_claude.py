#!/usr/bin/env python3
"""Benchmark persistent claude subprocess (raw CLI vs agent SDK).

Option A: raw `claude --input-format stream-json --output-format stream-json`
           kept alive across multiple prompts. Pay subprocess boot once.
Option B: claude_agent_sdk.ClaudeSDKClient with stripped config
           (no MCP, no tools, no session). Same idea via SDK wrapper.

Measures per-prompt TTFT + total for 10 sequential prompts each.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

PROMPT = "коротко скажи привіт"
NUM_PROMPTS = 10


# ---------- Option A: raw persistent claude CLI ----------

async def bench_raw_persistent() -> dict:
    """Spawn claude once, feed stream-json prompts via stdin."""
    args = [
        "claude",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--model", "haiku",
        "--dangerously-skip-permissions",
    ]
    boot_start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Wait for system:init event to know subprocess is ready
    ready = False
    boot_ms: float | None = None
    while not ready:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            evt = json.loads(line)
            if evt.get("type") == "system" and evt.get("subtype") == "init":
                boot_ms = (time.monotonic() - boot_start) * 1000
                ready = True
        except json.JSONDecodeError:
            continue

    ttfts, totals = [], []
    for i in range(NUM_PROMPTS):
        msg = {
            "type": "user",
            "message": {"role": "user", "content": PROMPT},
        }
        start = time.monotonic()
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()

        ttft: float | None = None
        # Read until we see a result (end-of-turn event)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            # first assistant message with text content = TTFT
            if ttft is None and evt.get("type") == "assistant":
                msg_body = evt.get("message") or {}
                content = msg_body.get("content") or []
                if any(b.get("type") == "text" and b.get("text") for b in content):
                    ttft = (time.monotonic() - start) * 1000
            # result event = end of turn
            if evt.get("type") == "result":
                break

        total = (time.monotonic() - start) * 1000
        if ttft is None:
            ttft = total
        ttfts.append(ttft)
        totals.append(total)
        print(f"  raw {i+1}/{NUM_PROMPTS}: ttft={ttft:.0f}ms total={total:.0f}ms", flush=True)

    proc.stdin.close()
    await proc.wait()
    return {
        "mode": "raw_persistent",
        "boot_ms": boot_ms,
        "ttfts": ttfts,
        "totals": totals,
        "ttft_median": statistics.median(ttfts),
        "total_median": statistics.median(totals),
        "ttft_first": ttfts[0],
        "ttft_warm_median": statistics.median(ttfts[1:]),
    }


# ---------- Option B: claude-agent-sdk stripped ----------

async def bench_sdk_stripped() -> dict:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    boot_start = time.monotonic()
    opts = ClaudeAgentOptions(
        allowed_tools=[],   # B: strip tools
        cwd=str(Path.cwd() / ".omc" / "bench-workspace"),
        permission_mode="bypassPermissions",
        model="haiku",
    )
    # seed empty .mcp.json to suppress MCP
    ws = Path(opts.cwd)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".mcp.json").write_text('{"mcpServers": {}}')

    client = ClaudeSDKClient(opts)
    await client.__aenter__()
    boot_ms = (time.monotonic() - boot_start) * 1000

    ttfts, totals = [], []
    for i in range(NUM_PROMPTS):
        start = time.monotonic()
        await client.query(PROMPT)
        ttft: float | None = None
        async for message in client.receive_response():
            if ttft is None and isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        ttft = (time.monotonic() - start) * 1000
                        break
            if isinstance(message, ResultMessage):
                break
        total = (time.monotonic() - start) * 1000
        if ttft is None:
            ttft = total
        ttfts.append(ttft)
        totals.append(total)
        print(f"  sdk {i+1}/{NUM_PROMPTS}: ttft={ttft:.0f}ms total={total:.0f}ms", flush=True)

    await client.__aexit__(None, None, None)
    return {
        "mode": "sdk_stripped",
        "boot_ms": boot_ms,
        "ttfts": ttfts,
        "totals": totals,
        "ttft_median": statistics.median(ttfts),
        "total_median": statistics.median(totals),
        "ttft_first": ttfts[0],
        "ttft_warm_median": statistics.median(ttfts[1:]),
    }


async def main() -> None:
    print("\n=== A: raw persistent claude CLI ===", flush=True)
    raw = await bench_raw_persistent()

    print("\n=== B: stripped agent SDK ===", flush=True)
    sdk = await bench_sdk_stripped()

    out = Path(".omc/benchmarks/phase1-persistent-claude.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    def fmt(r):
        return (
            f"| {r['mode']} | {r['boot_ms']:.0f} ms | {r['ttft_first']:.0f} ms | "
            f"{r['ttft_warm_median']:.0f} ms | {r['ttft_median']:.0f} ms | "
            f"{r['total_median']:.0f} ms |"
        )

    lines = [
        "# Phase 1 — persistent-claude TTFT benchmark",
        "",
        f"Prompt: `{PROMPT}` (×{NUM_PROMPTS} sequential sends per mode)",
        "Model: haiku",
        "",
        "## Results",
        "",
        "| mode | boot | first TTFT | warm TTFT median | all TTFT median | total median |",
        "|------|------|-----------|------------------|-----------------|--------------|",
        fmt(raw),
        fmt(sdk),
        "",
        "## Raw",
        "",
        f"**raw_persistent**: ttfts={raw['ttfts']}  totals={raw['totals']}",
        "",
        f"**sdk_stripped**: ttfts={sdk['ttfts']}  totals={sdk['totals']}",
        "",
    ]

    # Verdict: can any mode hit <=1500ms warm TTFT median?
    best = min(raw["ttft_warm_median"], sdk["ttft_warm_median"])
    winner = "raw_persistent" if raw["ttft_warm_median"] <= sdk["ttft_warm_median"] else "sdk_stripped"
    if best <= 1500:
        verdict = "GO"
        reason = f"{winner} warm TTFT median {best:.0f}ms <= 1500ms target"
    elif best <= 2500:
        verdict = "CONDITIONAL"
        reason = f"{winner} warm TTFT {best:.0f}ms — acceptable if we revise target to <=3s TTFA"
    else:
        verdict = "NO-GO"
        reason = f"{winner} warm TTFT {best:.0f}ms still over 2.5s — pivot required"

    lines += ["## Verdict", "", f"**{verdict}** — {reason}", ""]
    out.write_text("\n".join(lines))
    print(f"\n{'='*60}\nVERDICT: {verdict}\n{reason}\nWritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
