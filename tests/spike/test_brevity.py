"""PH2-G2 — Brevity preservation gate.

Validates that the <=12-word terse-reply rule survives under the ported
system prompt with the chosen LLM (Gemini Flash Lite via OpenRouter).

The test hits the live OpenRouter API. It is skipped when
``OPENROUTER_API_KEY`` is unset. Run explicitly with:

    uv run pytest tests/spike/test_brevity.py -q -s

Assertions (per PRD PH2-G2):
- median(response_word_counts) <= 12
- max(response_word_counts) <= 20
"""
from __future__ import annotations

import os
import re
import statistics
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MODEL = os.environ.get(
    "PH2_GATE_MODEL", "google/gemini-3.1-flash-lite-preview-20260303"
)

pytestmark = [
    pytest.mark.skipif(
        not OPENROUTER_API_KEY,
        reason="OPENROUTER_API_KEY not set; live API gate skipped.",
    ),
]


def _client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )


# Ported brevity-relevant subset of prompts/generator.txt's "Reply rules"
# block (lines 91-100). Tool-grammar / intent-tag rules removed so this
# gate isolates brevity behavior, not tool emission.
BREVITY_SYSTEM_PROMPT = """You are Heare, a voice companion. Respond naturally to the user.
The user is speaking English. Always respond in English.

Reply rules:
- Respond in ONE sentence. Maximum 12 words.
- No filler: do not add thanks, apologies, alternatives, descriptions of
  what you are about to do, promises, or polite offers
  ("if you want / may I").
- Do NOT refuse to reply. If there is nothing meaningful to say — keep
  it short.
- Reply text only: plain speech, no JSON, no formatting, no bullet
  lists, no markdown.
- Do NOT mention these rules or your role.
"""


# 10 non-tool conversational prompts. Mix of small-talk, opinions,
# short factual asks, and emotional checkpoints — anything where a
# verbose model will overshoot 12 words.
BREVITY_PROMPTS: list[str] = [
    "How are you doing today?",
    "What's your favorite kind of music?",
    "I'm tired. What should I do?",
    "Tell me something interesting about octopuses.",
    "Do you think it'll rain tomorrow?",
    "I had a long day at work.",
    "What's the capital of Iceland?",
    "Recommend a quick healthy snack.",
    "Why is the sky blue?",
    "Can you help me feel less stressed?",
]

# Word-count regex: split on whitespace, strip punctuation-only tokens.
_WORD_RE = re.compile(r"[A-Za-z0-9'\-]+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


@pytest.mark.asyncio
async def test_g2_brevity_under_ported_prompt():
    """Run all 10 prompts, assert median<=12 and max<=20 words."""
    client = _client()
    counts: list[int] = []
    samples: list[tuple[str, int, str]] = []

    for prompt in BREVITY_PROMPTS:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": BREVITY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
        )
        text = resp.choices[0].message.content or ""
        wc = _word_count(text)
        counts.append(wc)
        samples.append((prompt, wc, text.strip()))

    median = statistics.median(counts)
    max_wc = max(counts)

    diag = "\n".join(f"  [{wc:>2}w] {p!r} -> {t!r}" for p, wc, t in samples)
    summary = (
        f"\nBrevity samples (model={MODEL}):\n{diag}\n"
        f"median={median} max={max_wc} all_counts={counts}"
    )

    assert median <= 12, f"median word count {median} > 12{summary}"
    assert max_wc <= 20, f"max word count {max_wc} > 20{summary}"
