#!/usr/bin/env python3
"""
THROWAWAY SPIKE — Do not commit.
T1 (GATE): Validate that DeepSeek can reliably use tagged text for output typing.

V2: Improved detection (opening-tag-only fallback) + stronger system prompt.
"""

import httpx
import json
import os
import re
import sys
import time
from pathlib import Path

from src.config import load_env
load_env()

API_KEY  = os.environ["DEEPSEEK_API_KEY"]
MODEL    = os.environ.get("HEARE_LLM_MODEL", "deepseek-chat")
BASE_URL = "https://api.deepseek.com/v1"

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / ".sisyphus" / "evidence"
EVIDENCE_FILE = EVIDENCE_DIR / "task-1-tagged-text-results.txt"

# Two detection modes:
# 1. Complete pair: [voice]...[/voice]
# 2. Opening-only: [voice]... (no closing tag — fallback)

CLOSING_TAG_RE = re.compile(
    r"\[(voice|text|canvas)\]((?:(?!\[/(?:voice|text|canvas)\]).)*)\[/(voice|text|canvas)\]",
    re.DOTALL | re.IGNORECASE,
)

OPENING_TAG_RE = re.compile(
    r"^\s*\[(voice|text|canvas)\]\s*",
    re.IGNORECASE,
)

EXPECTED_TAGS = {
    "conversation":  "voice",
    "greeting":      "voice",
    "question":      "text",
    "list":          "text",
    "code":          "canvas",
    "chart":         "canvas",
    "explanation":   "voice",
    "fact":          "text",
    "visual_demo":   "canvas",
    "math":          "text",
    "advice":        "voice",
    "joke":          "voice",
    "weather":       "voice",
    "silent_mode":   "text",
    "silent_code":   "canvas",
}

SYSTEM_PROMPT = """You are Heare, a voice assistant. EVERY response you give MUST start with exactly one of these tags and end with the matching closing tag:

  [voice]your spoken response here[/voice]        — for greetings, conversation, explanations, advice, jokes
  [text]your written response here[/text]          — for facts, lists, references, math results
  [canvas]your HTML/CSS/JS code here[/canvas]       — for charts, diagrams, UI components, visual demos

CRITICAL RULES:
  - Your response MUST begin with an opening tag ([voice], [text], or [canvas]).
  - Your response MUST end with the matching closing tag ([/voice], [/text], or [/canvas]).
  - NEVER output text outside of a tag pair.
  - Choose ONE tag type per response. Do not mix.
  - The closing tag is MANDATORY. If you forget the closing tag, the response is broken."""

SILENT_SYSTEM = """You are Heare, a voice assistant. [voice] is UNAVAILABLE — you MUST NOT use it. EVERY response you give MUST start with one of these tags and end with the matching closing tag:

  [text]your written response here[/text]          — for ALL text responses (conversation, facts, lists, explanations, advice)
  [canvas]your HTML/CSS/JS code here[/canvas]       — ONLY for charts, diagrams, UI components, visual demos

CRITICAL RULES:
  - [voice] is FORBIDDEN. Never use [voice] or [/voice].
  - Use [text] for all conversational, factual, and explanatory responses.
  - Use [canvas] ONLY for visual/UI code.
  - Your response MUST begin with an opening tag ([text] or [canvas]).
  - Your response MUST end with the matching closing tag ([/text] or [/canvas]).
  - NEVER output text outside of a tag pair.
  - The closing tag is MANDATORY."""

PROMPTS = [
    ("conversation",   "How are you doing today?"),
    ("greeting",       "Hello!"),
    ("question",       "What is the capital of Ukraine?"),
    ("list",           "List 3 programming languages for AI"),
    ("code",           "Show me a simple HTML page with a heading"),
    ("chart",          "Create a bar chart showing sales: Jan=100, Feb=200, Mar=150"),
    ("explanation",    "Explain what quantum computing is"),
    ("fact",           "What is the speed of light in km/s?"),
    ("visual_demo",    "Create a countdown timer UI in HTML"),
    ("math",           "What is 15 * 37?"),
    ("advice",         "I'm feeling tired. What should I do?"),
    ("joke",           "Tell me a joke"),
    ("weather",        "What's the weather like?"),
    ("scientific",     "Explain the theory of relativity briefly"),
    ("comparison",     "Compare Python and JavaScript for web development"),
    ("silent_mode",    "Explain machine learning briefly",          "silent"),
    ("silent_code",    "Show me a CSS animation for a spinning loader", "silent"),
    ("silent_advice",  "I'm feeling stressed. Any tips?",           "silent"),
    ("silent_fact",    "What is the tallest mountain on Earth?",    "silent"),
]


def call_deepseek(system: str, user_message: str) -> dict:
    start = time.monotonic()
    try:
        with httpx.Client(timeout=45) as client:
            resp = client.post(
                f"{BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user_message},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2048,
                },
            )
        latency_ms = (time.monotonic() - start) * 1000
        if resp.status_code != 200:
            return {"text": "", "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return {"text": text, "latency_ms": latency_ms, "error": None}
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        return {"text": "", "latency_ms": latency_ms, "error": str(e)}


def detect_tag(text: str):
    """Returns {
        has_opening: bool,
        has_closing: bool,   # complete [tag]...[/tag]
        tag: str|None,       # inferred tag (from opening, or closing if present)
        inner: str,
    }"""
    text_stripped = text.strip()

    # Try complete pair first
    m = CLOSING_TAG_RE.search(text_stripped)
    if m:
        open_tag = m.group(1).lower()
        close_tag = m.group(3).lower()
        tag = open_tag if open_tag == close_tag else None
        return {
            "has_opening": True,
            "has_closing": True,
            "tag": tag,
            "inner": m.group(2).strip(),
        }

    # Fallback: opening tag only
    m2 = OPENING_TAG_RE.match(text_stripped)
    if m2:
        tag = m2.group(1).lower()
        inner = text_stripped[m2.end():].strip()
        return {
            "has_opening": True,
            "has_closing": False,
            "tag": tag,
            "inner": inner,
        }

    # No tag at all
    return {
        "has_opening": False,
        "has_closing": False,
        "tag": None,
        "inner": text_stripped,
    }


def tag_is_appropriate(test_id: str, detected_tag: str | None) -> tuple[bool, str]:
    expected = EXPECTED_TAGS.get(test_id)
    if expected is None:
        return True, "no_expectation"
    if detected_tag is None:
        return False, f"missing_tag (expected: {expected})"
    if detected_tag == expected:
        return True, f"correct ({expected})"
    return False, f"wrong_tag (got: {detected_tag}, expected: {expected})"


def run():
    print("=" * 80)
    print("T1 GATE V2: Tagged Text Compliance — DeepSeek (deepseek-chat)")
    print("Detection: opening-tag fallback + max_tokens=2048 + stronger prompt")
    print("=" * 80)

    results = []
    total = len(PROMPTS)
    opening_count = 0
    closing_count = 0
    appropriate_count = 0
    errors = 0
    silent_voice_violations = 0
    total_latency = 0.0

    for i, entry in enumerate(PROMPTS):
        test_id = entry[0]
        message = entry[1]
        mode = entry[2] if len(entry) > 2 else "normal"
        system = SYSTEM_PROMPT if mode == "normal" else SILENT_SYSTEM

        print(f"\n[{i+1}/{total}] {test_id} ({mode})")
        print(f"  Prompt: {message}")

        out = call_deepseek(system, message)
        text = out["text"]
        lat = out["latency_ms"]
        err = out["error"]

        if err:
            print(f"  ❌ ERROR: {err}")
            errors += 1
            results.append({
                "test_id": test_id, "mode": mode, "prompt": message,
                "raw_text": f"[ERROR] {err}", "tag_info": None,
                "latency_ms": lat, "error": err,
            })
            continue

        tag_info = detect_tag(text)
        tag = tag_info["tag"]
        has_opening = tag_info["has_opening"]
        has_closing = tag_info["has_closing"]
        is_appropriate, reason = tag_is_appropriate(test_id, tag)

        silent_violation = mode == "silent" and tag == "voice"
        if silent_violation:
            silent_voice_violations += 1

        if has_opening:
            opening_count += 1
        if has_closing:
            closing_count += 1
        if is_appropriate:
            appropriate_count += 1
        total_latency += lat

        if has_opening and is_appropriate:
            status = "✅"
        elif has_opening:
            status = "⚠️"
        else:
            status = "❌"

        close_marker = "+close" if has_closing else "-close"
        print(f"  {status} tag={tag or 'NONE'} [{close_marker}] | appropriate={is_appropriate} ({reason}) | {lat:.0f}ms")
        preview = tag_info["inner"][:130]
        print(f"  Content: {preview}{'...' if len(tag_info['inner']) > 130 else ''}")

        results.append({
            "test_id": test_id, "mode": mode, "prompt": message,
            "raw_text": text, "tag_info": tag_info,
            "latency_ms": lat, "tag_appropriate": is_appropriate,
            "appropriateness_reason": reason,
            "silent_voice_violation": silent_violation,
        })

    valid = total - errors
    avg_lat = total_latency / valid if valid > 0 else 0
    opening_pct = (opening_count / valid * 100) if valid > 0 else 0
    closing_pct = (closing_count / valid * 100) if valid > 0 else 0
    appropriate_pct = (appropriate_count / valid * 100) if valid > 0 else 0

    gate = "✅ PROCEED with tagged-text approach" if opening_pct >= 80 else \
           "⛔ FALL BACK to tool-call approach"

    print("\n" + "=" * 80)
    print("GATE SUMMARY (V2)")
    print("=" * 80)
    print(f"  Total prompts:          {total}")
    print(f"  Errors:                 {errors}")
    print(f"  Valid responses:        {valid}")
    print(f"  Has opening tag:        {opening_count} ({opening_pct:.1f}%)")
    print(f"  Has closing tag:        {closing_count} ({closing_pct:.1f}%)")
    print(f"  Appropriate tag:        {appropriate_count} ({appropriate_pct:.1f}%)")
    print(f"  Avg latency:            {avg_lat:.0f}ms")
    print(f"  Silent [voice] violations: {silent_voice_violations}")
    print(f"\n  GATE (opening tag >= 80%): {gate}")
    print("=" * 80)

    # Write evidence
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVIDENCE_FILE, "w") as f:
        f.write("=" * 80 + "\n")
        f.write("T1 GATE V2: Tagged Text Compliance Evidence\n")
        f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Model: {MODEL} @ {BASE_URL}\n")
        f.write("Detection: opening-tag fallback (opening-only accepted), max_tokens=2048\n")
        f.write("=" * 80 + "\n\n")

        f.write("## PER-PROMPT RESULTS\n\n")
        for r in results:
            f.write(f"--- {r['test_id']} ({r['mode']}) ---\n")
            f.write(f"Prompt:   {r['prompt']}\n")
            ti = r.get("tag_info")
            if ti:
                f.write(f"Tag:      {ti['tag'] or 'NONE'} (opening={ti['has_opening']}, closing={ti['has_closing']})\n")
                f.write(f"Appropriate: {r['tag_appropriate']} ({r['appropriateness_reason']})\n")
                if r.get('silent_voice_violation'):
                    f.write(f"⚠️ SILENT MODE [voice] VIOLATION\n")
                f.write(f"Latency:  {r['latency_ms']:.0f}ms\n")
                f.write(f"Inner:    {ti['inner'][:300]}\n")
            else:
                f.write(f"ERROR:    {r.get('error', 'unknown')}\n")
            raw = r['raw_text']
            if len(raw) > 400:
                f.write(f"Raw:      {raw[:400]}...\n")
            else:
                f.write(f"Raw:      {raw}\n")
            f.write("\n")

        f.write("=" * 80 + "\n")
        f.write("## SUMMARY STATISTICS\n")
        f.write("=" * 80 + "\n")
        f.write(f"Total prompts:          {total}\n")
        f.write(f"Errors:                 {errors}\n")
        f.write(f"Valid responses:        {valid}\n")
        f.write(f"Has opening tag:        {opening_count} ({opening_pct:.1f}%)\n")
        f.write(f"Has closing tag:        {closing_count} ({closing_pct:.1f}%)\n")
        f.write(f"Appropriate tag:        {appropriate_count} ({appropriate_pct:.1f}%)\n")
        f.write(f"Avg latency:            {avg_lat:.0f}ms\n")
        f.write(f"Silent [voice] violations: {silent_voice_violations}\n")
        f.write("\n")
        f.write("=" * 80 + "\n")
        f.write(f"## GATE DECISION\n")
        f.write("=" * 80 + "\n")
        f.write(f"Threshold: >= 80% opening-tag compliance\n")
        f.write(f"Result:    {opening_pct:.1f}%\n")
        f.write(f"Decision:  {gate}\n")
        f.write("\n")
        f.write("## SYSTEM PROMPT (normal)\n")
        f.write(f"```\n{SYSTEM_PROMPT}\n```\n\n")
        f.write("## SYSTEM PROMPT (silent)\n")
        f.write(f"```\n{SILENT_SYSTEM}\n```\n")

    print(f"\n📄 Evidence saved to: {EVIDENCE_FILE}")
    return opening_pct, gate


if __name__ == "__main__":
    compliance, gate = run()
    sys.exit(0 if compliance >= 80 else 1)
