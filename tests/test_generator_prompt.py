"""Golden verification of prompts/generator.txt substitution shape."""
from __future__ import annotations

from pathlib import Path

from src.context import _safe_substitute


TEMPLATE_PATH = Path(__file__).parent.parent / "prompts" / "generator.txt"


def _load() -> str:
    return TEMPLATE_PATH.read_text()


def test_template_exists_and_nonempty() -> None:
    raw = _load()
    assert raw.strip(), "generator.txt is empty"


def test_template_has_exactly_expected_placeholders() -> None:
    raw = _load()
    import re

    found = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", raw))
    assert found == {"time", "timezone", "persona", "recent_transcripts", "transcript"}


def test_template_does_not_contain_phase2_placeholders() -> None:
    raw = _load()
    for forbidden in (
        "{conversation_summary}",
        "{active_topics}",
        "{entities}",
        "{recent_turns}",
    ):
        assert forbidden not in raw, f"unexpected Phase-2 placeholder {forbidden!r}"


def test_template_requires_ukrainian_response() -> None:
    raw = _load()
    # Basic sanity: the prompt must instruct Ukrainian output
    assert "україн" in raw.lower()


def test_template_reply_text_forbids_json() -> None:
    """Reply text must still be plain Ukrainian; JSON is allowed ONLY inside
    <intent> tags (Phase 2.1)."""
    raw = _load()
    # The rule must appear: reply text has no JSON
    assert "без JSON" in raw or "НЕ виводь JSON" in raw


def test_template_has_intents_section() -> None:
    """Phase 2.1 US-P2.1-02: INTENTS section documents when/how to emit tags."""
    raw = _load()
    assert "INTENTS" in raw
    assert "<intent>" in raw
    assert '"tool"' in raw
    assert '"args"' in raw


def test_template_forbids_fenced_intent() -> None:
    """Prompt must explicitly say don't wrap intent tags in ```fences```."""
    raw = _load()
    assert "fences" in raw.lower() or "```" in raw


def test_substitution_leaves_no_placeholders() -> None:
    raw = _load()
    ctx = {
        "persona": "Ти Heare, теплий співрозмовник.",
        "time": "2026-04-18 01:23:45",
        "timezone": "Europe/Kiev",
        "recent_transcripts": "  - [01:23:00] Привіт.",
        "transcript": "Як справи?",
    }
    rendered = _safe_substitute(raw, ctx)
    import re

    # No placeholder-shaped braces remaining
    leftover = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", rendered)
    assert leftover == [], f"leftover placeholders: {leftover}"
    # All context values present in rendered output
    for value in ctx.values():
        assert value.strip().split("\n")[0] in rendered
