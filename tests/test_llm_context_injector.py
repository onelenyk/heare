"""Tests for llm_context_injector — per-turn system prompt rebuild (PH2-07)."""
from __future__ import annotations




from src.agent.llm.context_injector import (  # noqa: E402
    render_native_system_prompt,
)


# ---------------------------------------------------------------------------
# render_native_system_prompt — pure function


def test_render_handles_language_tag() -> None:
    out = render_native_system_prompt(persona="", context=None, language="uk")
    assert "Ukrainian" in out
    assert "12 words" in out


def test_render_handles_full_language_name() -> None:
    out = render_native_system_prompt(
        persona="", context=None, language="Ukrainian"
    )
    assert "Ukrainian" in out


def test_render_includes_persona() -> None:
    out = render_native_system_prompt(
        persona="I am Heare. Quick replies only.",
        context=None,
        language="en",
    )
    assert "I am Heare. Quick replies only." in out


def test_render_falls_back_when_persona_empty() -> None:
    out = render_native_system_prompt(
        persona="", context=None, language="en"
    )
    assert "HARD CONSTRAINTS" in out


def test_render_no_intent_grammar() -> None:
    out = render_native_system_prompt(
        persona="", context=None, language="en"
    )
    forbidden = ["<intent>", "</intent>", '"tool":', '"args":']
    for token in forbidden:
        assert token not in out, f"intent grammar leaked: {token!r}"


def test_render_includes_context_blocks() -> None:
    ctx = {
        "time": "12:00:00",
        "timezone": "UTC",
        "recent_transcripts": "  - [11:59:00] hello",
        "conversation_summary": "User asked about chili.",
        "active_topics": "chili, vegan",
        "entities": "Reykjavik",
        "recent_turns": "  - hi / hello",
        "recent_actions": "  - [11:59:30] web_search 'chili recipe' (2m ago)",
        "mcp_servers": "MCP servers: linear, github",
    }
    out = render_native_system_prompt(
        persona="P", context=ctx, language="en"
    )
    assert "Current time: 12:00:00 (UTC)" in out
    assert "User asked about chili" in out
    assert "chili, vegan" in out
    assert "Reykjavik" in out
    assert "web_search 'chili recipe'" in out
    assert "MCP servers: linear, github" in out


def test_render_includes_host_os_line() -> None:
    out = render_native_system_prompt(persona="", context=None, language="en")
    assert "Host OS:" in out


def test_environment_questions_are_delegated_not_run_directly() -> None:
    """The conversational agent does not run shell commands; it hands them
    to the worker. The prompt has to say so, and say it first."""
    out = render_native_system_prompt(persona="", context=None, language="en")
    assert "Available tools:" in out, "tool catalog section missing"
    assert "delegate" in out, "delegate not found in prompt"
    assert "bash" not in out, "the voice agent must not be offered bash"
    # Hard constraints must appear before tool catalog — constraints first.
    hc_idx = out.find("HARD CONSTRAINTS")
    tc_idx = out.find("Available tools:")
    assert hc_idx != -1, "HARD CONSTRAINTS section missing"
    assert tc_idx != -1, "Available tools section missing"
    assert hc_idx < tc_idx, "HARD CONSTRAINTS must precede tool catalog"


def test_render_skips_empty_context_blocks() -> None:
    ctx = {
        "time": "12:00:00",
        "recent_transcripts": "(none)",
        "recent_actions": "(none)",
    }
    out = render_native_system_prompt(
        persona="", context=ctx, language="en"
    )
    assert "Recent transcripts:" not in out
    assert "Recent actions:" not in out
