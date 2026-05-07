"""PH2-G3 — Multilingual accuracy gate.

Validates UK/RU/EN reply quality matches the current bar by checking
that the model replies in the same script as the prompt for at least
14 of 15 prompts. Uses ``src.voice.language.core.detect_script_language`` (which
returns ``'cyrillic' | 'latin' | 'unknown'``) — Ukrainian and Russian
both fall under cyrillic, English under latin.

The test hits the live OpenRouter API. It is skipped when
``OPENROUTER_API_KEY`` is unset. Run explicitly with:

    uv run pytest tests/spike/test_multilingual.py -q -s

Assertion (per PRD PH2-G3):
- >=14/15 responses match input language (script).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.voice.language.core import detect_script_language

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


def _system_prompt(language_label: str) -> str:
    """Mirrors prompts/generator.txt's language directive."""
    return (
        "You are Heare, a voice companion. Respond naturally to the user.\n"
        f"The user is speaking {language_label}. Always respond in "
        f"{language_label}.\n"
        "If the detected language is uncertain, respond in English.\n\n"
        "Reply rules:\n"
        "- Respond in ONE sentence. Maximum 12 words.\n"
        "- Plain speech only — no JSON, no formatting.\n"
    )


# 5 prompts per language. Picked to mix small-talk + substantive
# questions so a one-word reply doesn't trivially satisfy the script
# check.
PROMPTS: list[tuple[str, str, str]] = [
    # (language_label, expected_script, prompt)
    ("English", "latin", "How are you doing today?"),
    ("English", "latin", "What's a good book to read tonight?"),
    ("English", "latin", "Tell me a fun fact about space."),
    ("English", "latin", "I'm tired — any advice?"),
    ("English", "latin", "What time is it in Tokyo right now?"),
    ("Ukrainian", "cyrillic", "Як справи сьогодні?"),
    ("Ukrainian", "cyrillic", "Порадь хорошу книгу на вечір."),
    ("Ukrainian", "cyrillic", "Розкажи цікавий факт про космос."),
    ("Ukrainian", "cyrillic", "Я втомився — що порадиш?"),
    ("Ukrainian", "cyrillic", "Котра година зараз у Токіо?"),
    ("Russian", "cyrillic", "Как дела сегодня?"),
    ("Russian", "cyrillic", "Посоветуй хорошую книгу на вечер."),
    ("Russian", "cyrillic", "Расскажи интересный факт про космос."),
    ("Russian", "cyrillic", "Я устал — что посоветуешь?"),
    ("Russian", "cyrillic", "Который час сейчас в Токио?"),
]


@pytest.mark.asyncio
async def test_g3_multilingual_script_match():
    """Run all 15 prompts, assert >=14/15 reply scripts match expected."""
    client = _client()
    matches = 0
    samples: list[tuple[str, str, str, str, bool]] = []

    for label, expected_script, prompt in PROMPTS:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _system_prompt(label)},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
        )
        text = (resp.choices[0].message.content or "").strip()
        observed = detect_script_language(text)
        ok = observed == expected_script
        matches += int(ok)
        samples.append((label, expected_script, observed, text, ok))

    diag = "\n".join(
        f"  {'OK ' if ok else 'BAD'} [{label:9} expect={exp:8} got={obs:8}] "
        f"{text!r}"
        for label, exp, obs, text, ok in samples
    )
    summary = f"\nMultilingual samples (model={MODEL}):\n{diag}\nmatches={matches}/15"

    assert matches >= 14, f"multilingual match rate {matches}/15 < 14/15{summary}"
