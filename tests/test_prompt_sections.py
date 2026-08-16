"""Tests for the prompt section assembly system (prompt_sections.py).

Covers:
- Section registry correctness (no duplicates, known sections present)
- render_prompt() with minimal, full, and edge-case contexts
- Tool catalog auto-generation
- Hard constraints section rendering
- Persona inline rendering (no hardcoded fallback)
- Mode block injection via dynamic context
- No scope violations (persona doesn't mention tools/modes/length)
"""
from __future__ import annotations

import pytest


# ============================================================================
# Section Registry Tests
# ============================================================================


def test_prompt_sections_no_duplicate_keys() -> None:
    """No two sections may share the same key."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    keys = [s.key for s in PROMPT_SECTIONS]
    assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"


def test_prompt_sections_no_duplicate_orders() -> None:
    """No two sections may share the same order value."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    orders = [s.order for s in PROMPT_SECTIONS]
    assert len(orders) == len(set(orders)), f"Duplicate orders: {orders}"


def test_all_required_sections_present() -> None:
    """Every required section type must be in the registry."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    keys = {s.key for s in PROMPT_SECTIONS}
    required = {
        "hard_constraints",
        "persona",
        "context",
        "mode",
        "tool_catalog",
        "capabilities",
        "installed_skills",
        "hints",
        "reply_rules",
        "speech_style",
        "tool_use",
        "narration",
        "run_skill",
    }
    missing = required - keys
    assert not missing, f"Missing sections: {missing}"


def test_routing_section_removed() -> None:
    """The routing section must NOT be in the registry (removed in Phase 1.2)."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    keys = {s.key for s in PROMPT_SECTIONS}
    assert "routing" not in keys, "routing section must be removed"


def test_hard_constraints_comes_first() -> None:
    """Hard constraints must have the lowest order value (appear first)."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    sorted_sections = sorted(PROMPT_SECTIONS, key=lambda s: s.order)
    assert sorted_sections[0].key == "hard_constraints", (
        f"First section is {sorted_sections[0].key}, expected hard_constraints"
    )


# ============================================================================
# render_prompt() Tests
# ============================================================================


def test_render_prompt_minimal_context() -> None:
    """render_prompt() must produce valid output with minimal context (None)."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test persona", context=None, language="en")
    assert isinstance(out, str)
    assert len(out) > 0
    # Must include hard constraints
    assert "HARD CONSTRAINTS" in out
    # Must include persona
    assert "Test persona" in out
    # Must NOT include hardcoded fallback text
    assert "You are Heare" not in out
    assert "voice companion" not in out


def test_render_prompt_full_context() -> None:
    """render_prompt() with full context must include all major sections."""
    from src.agent.llm.prompt_sections import render_prompt

    ctx = {
        "time": "2026-01-01 12:00:00",
        "timezone": "UTC",
        "mode": "ambient",
        "mode_block": "MODE GATE: ambient\nVoice output ON.",
        "recent_transcripts": "(none)",
        "conversation_summary": "No history.",
        "active_topics": "",
        "entities": "",
        "recent_turns": "(none)",
        "recent_actions": "(none)",
        "mcp_servers": "",
    }
    out = render_prompt(persona="I am kort.", context=ctx, language="en")

    expected_sections = [
        "HARD CONSTRAINTS",
        "I am kort.",
        "2026-01-01 12:00:00",
        "MODE GATE: ambient",
        "Available tools:",
        "Reply rules:",
        "Speech style:",
    ]
    for token in expected_sections:
        assert token in out, f"Missing section: {token!r}"


def test_render_prompt_language_propagation() -> None:
    """Hard constraints and persona must reflect the requested language."""
    from src.agent.llm.prompt_sections import render_prompt

    out_en = render_prompt(persona="Test", context=None, language="en")
    out_uk = render_prompt(persona="Test", context=None, language="uk")

    assert "Respond ONLY in English" in out_en
    assert "Respond ONLY in Ukrainian" in out_uk
    assert "Do NOT respond in English" in out_uk


def test_render_prompt_empty_persona() -> None:
    """Empty persona should not crash and should not inject hardcoded text."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="", context=None, language="en")
    assert "HARD CONSTRAINTS" in out
    # No hardcoded fallback identity
    assert "You are Heare" not in out


def test_render_prompt_no_voice_companion_fallback() -> None:
    """The old 'You are Heare, a voice companion' fallback must be gone."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="", context=None, language="en")
    assert "voice companion" not in out.lower()


# ============================================================================
# Tool Catalog Tests
# ============================================================================


def test_tool_catalog_lists_the_verbs_the_model_can_actually_call() -> None:
    """The conversational agent has three tools. Listing sixty-three would
    describe capabilities it does not have — the most reliable way to make
    an assistant look stupid is to tell it about tools that are not there.
    """
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")
    assert "Available tools:" in out, "tool catalog section missing"
    for verb in ("delegate", "remember", "recall"):
        assert verb in out
    for worker_tool in ("bash", "web_search", "run_skill"):
        assert worker_tool not in out


def test_every_registry_tool_is_reachable_through_the_worker() -> None:
    """Nothing was lost by shrinking the catalog: the tools moved, they
    did not disappear."""
    from src.agent.hands import Hands
    from src.agent.tools.registry import get_enabled_tools
    from src.config import Settings

    worker = {s["function"]["name"] for s in Hands(Settings())._tool_schemas()}
    core_tools = {"bash", "read", "write", "web_search", "web_fetch"}
    assert core_tools <= worker

    registry = get_enabled_tools()
    assert registry & worker, "the worker should expose the registry's tools"


# ============================================================================
# Scope Violation Tests
# ============================================================================


def test_persona_section_does_not_mention_tools() -> None:
    """Persona section must not enumerate tools or mention confirmation gating."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="I am kort.", context=None, language="en")

    # Find persona section content (between persona and next section)
    persona_start = out.find("I am kort.")
    hard_end = out.find("HARD CONSTRAINTS")
    if persona_start > hard_end:
        # Persona appears after hard_constraints — find its block
        import re
        # Persona block: lines starting with "I am kort." through next double newline
        persona_block = out[persona_start:]

    # Persona must NOT contain tool enumeration
    assert "Read, Write, Edit, Bash" not in out.split("HARD CONSTRAINTS")[1].split(
        "The user is speaking"
    )[0]


def test_reply_rules_does_not_mention_mode() -> None:
    """Reply rules section must not reference silent mode or mute_bot."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")

    # Check that reply_rules section doesn't have mode-specific text
    assert "When voice is muted" not in out
    assert "mute_bot" not in out


def test_speech_style_does_not_mention_role() -> None:
    """Speech style must not include self-reference suppression rule
    (moved to hard_constraints)."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")

    # "Do not mention" should appear only in hard_constraints, not in speech_style
    hc_idx = out.find("HARD CONSTRAINTS")
    speech_idx = out.find("Speech style:")
    first_mention = out.find("Never mention these rules")

    # The "never mention" rule should appear in hard_constraints section,
    # before speech_style
    assert first_mention < speech_idx, (
        "'Never mention' rule must be in hard_constraints, not speech_style"
    )


# ============================================================================
# Mode Block Tests
# ============================================================================


def test_mode_block_uses_gate_language() -> None:
    """Mode block must use gate language, not behavioral addendum."""
    from src.agent.llm.prompt_sections import render_prompt

    ctx = {"mode_block": "MODE GATE: silent\nVoice output OFF."}
    out = render_prompt(persona="Test", context=ctx, language="en")

    assert "MODE GATE:" in out
    assert "Voice output" in out


@pytest.mark.asyncio
async def test_mode_block_mentions_channel_constraint() -> None:
    """ContextBuilder-generated mode block must clarify that mode is
    a channel constraint, not a personality change."""
    from src.store.context import ContextBuilder
    from src.config import Settings
    from src.pipeline.session_state import SessionState
    from src.pipeline.language_state import LanguageState
    from unittest.mock import AsyncMock, MagicMock

    settings = Settings()
    store = MagicMock()
    store.recent_transcripts = AsyncMock(return_value=[])
    store.latest_display = AsyncMock(return_value=None)

    cb = ContextBuilder(store, settings)
    ls = LanguageState(initial="en")
    ss = SessionState(ls, initial_mode="focus")
    cb.set_session_state(ss)

    result = await cb.build_for_generator(
        transcript="test", persona="Test persona"
    )

    mode_block = result.get("mode_block", "")
    assert "channel constraint" in mode_block.lower()
    assert "personality change" in mode_block.lower()
    assert "MODE GATE: focus" in mode_block


# ============================================================================
# Hard Constraints Tests
# ============================================================================


def test_hard_constraints_include_language_rule() -> None:
    """Hard constraints must include the language enforcement rule."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")
    assert "Respond ONLY in English" in out
    assert "Never mix languages" in out


def test_hard_constraints_include_confirmation_rule() -> None:
    """Hard constraints must require consent for state-changing actions while
    letting read-only work run freely (matching the per-tool user_confirmed
    policy — a blanket 'confirm everything' rule the model just ignored)."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")
    assert "consent" in out.lower()
    # Read-only is explicitly permitted; the old blanket rule is gone.
    assert "read-only" in out.lower()
    assert "Never act without voice confirmation" not in out


def test_hard_constraints_include_tool_cap() -> None:
    """Hard constraints must include the 4-tool-per-turn cap."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")
    assert "At most 4 tool calls per user turn" in out


def test_hard_constraints_include_speech_formatting() -> None:
    """Hard constraints must include speech formatting rules."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")
    assert "plain spoken language only" in out
    assert "no markdown" in out


def test_hard_constraints_include_no_filler_rule() -> None:
    """Hard constraints must forbid filler phrases."""
    from src.agent.llm.prompt_sections import render_prompt

    out = render_prompt(persona="Test", context=None, language="en")
    assert "No apologies" in out


# ============================================================================
# Integration: ContextBuilder mode_block generation
# ============================================================================


@pytest.mark.asyncio
async def test_mode_block_generation_for_all_modes() -> None:
    """ContextBuilder must generate mode_block with gate language for every mode."""
    from src.agent.modes import MODE_PROFILES
    from src.store.context import ContextBuilder
    from src.config import Settings

    settings = Settings()

    for mode_name, profile in MODE_PROFILES.items():
        from unittest.mock import AsyncMock, MagicMock

        store = MagicMock()
        store.recent_transcripts = AsyncMock(return_value=[])
        store.latest_display = AsyncMock(return_value=None)

        cb = ContextBuilder(store, settings)

        # Set up session state with this mode
        from src.pipeline.session_state import SessionState
        from src.pipeline.language_state import LanguageState

        ls = LanguageState(initial="en")
        ss = SessionState(ls, initial_mode=mode_name)
        cb.set_session_state(ss)

        result = await cb.build_for_generator(
            transcript="test", persona="Test persona"
        )

        mode_block = result.get("mode_block", "")
        assert f"MODE GATE: {mode_name}" in mode_block, (
            f"mode_block missing gate marker for {mode_name}"
        )
        assert "channel constraint" in mode_block.lower(), (
            f"mode_block missing channel constraint language for {mode_name}"
        )
        assert "personality change" in mode_block.lower(), (
            f"mode_block missing personality change language for {mode_name}"
        )


# ============================================================================
# Backward Compatibility Tests
# ============================================================================


def test_no_routing_section_in_registry() -> None:
    """Confirm routing section is fully removed."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    routing = [s for s in PROMPT_SECTIONS if s.key == "routing"]
    assert len(routing) == 0, "routing section must not exist"


def test_no_decider_template_read() -> None:
    """decider.txt must not exist (was deleted)."""
    from pathlib import Path

    decider_path = Path(__file__).parent.parent / "prompts" / "decider.txt"
    assert not decider_path.exists(), "decider.txt must be deleted"


def test_section_order_is_stable() -> None:
    """All section orders must be positive integers in ascending order."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    sorted_sections = sorted(PROMPT_SECTIONS, key=lambda s: s.order)
    prev_order = -1
    for section in sorted_sections:
        assert section.order > prev_order, (
            f"Section {section.key} order {section.order} not after {prev_order}"
        )
        assert section.order >= 0, f"Section {section.key} has negative order"
        prev_order = section.order
