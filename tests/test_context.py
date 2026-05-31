"""Tests for src/context.py ContextBuilder."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.config import Settings, load_settings
from src.store.context import ContextBuilder
from src.store.storage import TranscriptStore


@pytest.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "heare.db"
        s = TranscriptStore(db)
        await s.init()
        try:
            yield s
        finally:
            await s.close()


async def test_build_shape_on_transcript(store: TranscriptStore) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("привіт, що робиш?", heartbeat=False)
    assert set(result.keys()) == {
        "time",
        "timezone",
        "mode",
        "heartbeat_flag",
        "recent_transcripts",
        "transcript_or_heartbeat",
        "silence_block",
        "proactivity_block",
        "conversation_active",
        "conversation_summary",
        "active_topics",
        "entities",
        "recent_turns",
    }
    assert result["heartbeat_flag"] == "no"
    assert "привіт" in result["transcript_or_heartbeat"]


async def test_build_shape_on_heartbeat(store: TranscriptStore) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(None, heartbeat=True)
    assert result["heartbeat_flag"] == "yes"
    assert "heartbeat" in result["transcript_or_heartbeat"]


async def test_recent_transcripts_rendering(store: TranscriptStore) -> None:
    await store.log_transcript("один", "ambient")
    await store.log_transcript("два", "ambient")
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("три", heartbeat=False)
    assert "один" in result["recent_transcripts"]
    assert "два" in result["recent_transcripts"]


async def test_live_mcp_bridge_block_overrides_static(
    store: TranscriptStore,
) -> None:
    """When a connected bridge is attached, build_for_generator must
    advertise its live tool block instead of the static .mcp.json one."""

    class _FakeBridge:
        def prompt_block(self) -> str:
            return "Connected MCP servers (1) — callable: mcp__x__do"

    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    ctx._mcp_descriptions = "STATIC should not appear"
    ctx.set_mcp_bridge(_FakeBridge())
    result = await ctx.build_for_generator("hi", persona="p")
    assert result["mcp_servers"] == (
        "Connected MCP servers (1) — callable: mcp__x__do"
    )


async def test_current_display_injected_when_present(
    store: TranscriptStore,
) -> None:
    """build_for_generator surfaces the latest show_display content so the
    agent is aware of what is on screen (two-way wiring)."""
    settings = load_settings()
    await store.log_display("col1 | col2\n1 | 2", "table", title="metrics")
    ctx = ContextBuilder(store, settings)
    result = await ctx.build_for_generator("hi", persona="p")
    cd = result["current_display"]
    assert "metrics" in cd
    assert "col1 | col2" in cd
    assert "format=table" in cd


async def test_current_display_absent_when_no_display(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build_for_generator("hi", persona="p")
    assert "current_display" not in result


async def test_static_mcp_block_used_when_bridge_empty(
    store: TranscriptStore,
) -> None:
    class _EmptyBridge:
        def prompt_block(self) -> str:
            return ""

    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    ctx._mcp_descriptions = "STATIC fallback"
    ctx.set_mcp_bridge(_EmptyBridge())
    result = await ctx.build_for_generator("hi", persona="p")
    assert result["mcp_servers"] == "STATIC fallback"


async def test_render_real_decider_template(store: TranscriptStore) -> None:
    """prompts/decider.txt contains literal { and } from the JSON example.
    render() must substitute named placeholders without choking on braces."""
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("привіт", heartbeat=False)
    template = Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    rendered = ctx.render(template, result)
    assert "{mode}" not in rendered
    assert "{transcript_or_heartbeat}" not in rendered
    # JSON-literal braces in the example schemas must survive rendering
    # without being treated as format placeholders.
    assert '{"t":"n"}' in rendered
    assert '"t":"s"' in rendered
    assert '"r":' in rendered
    assert "привіт" in rendered


def test_decider_prompt_has_length_constraints() -> None:
    """LAT-B2: prompt must enforce reply word limit and MAX 5 words intent."""
    template = Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    assert "MAX 15 words" in template, "reply length constraint missing"
    assert "MAX 5 words" in template, "intent length constraint missing"


def test_decider_prompt_mandates_strict_key_order() -> None:
    """LAT-B2: prompt must declare strict field ordering (t first, r second)."""
    template = Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    assert "FIELD ORDER IS STRICT" in template
    # Must mention that 't' is first and 'r' follows it
    assert '"t"' in template
    assert '"r"' in template
    # The ordering section must explicitly name both keys in order
    strict_section = template[template.index("FIELD ORDER IS STRICT"):]
    assert strict_section.index('"t"') < strict_section.index('"r"')


async def test_build_keeps_transcript_placeholder_literal(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(
        "x", heartbeat=False, keep_placeholders=["transcript_or_heartbeat"]
    )
    assert result["transcript_or_heartbeat"] == "{transcript_or_heartbeat}"


async def test_golden_string_render(store: TranscriptStore) -> None:
    """Golden-file rendering must be byte-stable across runs.

    First run captures the rendered output (with a fixed ctx) into
    tests/fixtures/decider_prompt.golden.txt. Subsequent runs
    re-render and diff against the golden.
    """
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    # Fixed deterministic context — do NOT call ctx.build() because it
    # renders the current time, which breaks byte-stability.
    fixed_ctx = {
        "time": "2026-04-13 12:00:00",
        "timezone": "UTC",
        "mode": "ambient",
        "heartbeat_flag": "no",
        "recent_transcripts": "(none)",
        "transcript_or_heartbeat": "тест",
        "silence_block": "",
        "proactivity_block": "",
    }
    template = (
        Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    )
    rendered = ctx.render(template, fixed_ctx)

    golden_path = (
        Path(__file__).parent / "fixtures" / "decider_prompt.golden.txt"
    )
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    if not golden_path.exists():
        golden_path.write_text(rendered)
    else:
        expected = golden_path.read_text()
        assert rendered == expected, (
            "flag-off rendered prompt drifted from golden. "
            f"Delete {golden_path} to regenerate if the drift is intentional."
        )


def test_render_with_template() -> None:
    import asyncio

    async def _run():
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "heare.db"
            s = TranscriptStore(db)
            await s.init()
            try:
                settings = load_settings()
                ctx = ContextBuilder(s, settings)
                result = await ctx.build("hi", heartbeat=False)
                template = "mode={mode} input={transcript_or_heartbeat}"
                rendered = ctx.render(template, result)
                assert "mode=" in rendered
                assert "hi" in rendered
            finally:
                await s.close()

    asyncio.run(_run())


async def test_conversation_context_rendering(store: TranscriptStore) -> None:
    """Test that conversation context variables are properly rendered in the context."""
    settings = load_settings()
    ctx = ContextBuilder(store, settings)

    # Test with no conversation (default case)
    result = await ctx.build("test transcript", heartbeat=False)

    # Verify new conversation context variables are present
    assert "conversation_active" in result
    assert "conversation_summary" in result
    assert "active_topics" in result
    assert "entities" in result
    assert "recent_turns" in result

    # Verify default values
    assert result["conversation_active"] == "no"
    assert result["conversation_summary"] == ""
    assert result["active_topics"] == ""
    assert result["entities"] == ""
    assert result["recent_turns"] == "(none)"

    # Verify they are rendered in template substitution
    template = "conversation_active={conversation_active} active_topics={active_topics}"
    rendered = ctx.render(template, result)
    assert "conversation_active=no" in rendered
    assert "active_topics=" in rendered


async def test_build_for_generator_returns_minimal_keys(store: TranscriptStore) -> None:
    """Phase 2.2: bfg returns the canonical-key superset; ``mcp_servers``
    appears optionally when MCP servers are configured in the workspace."""
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build_for_generator(transcript="як справи?", persona="Ти Heare.")
    required = {
        "time",
        "timezone",
        "persona",
        "recent_transcripts",
        "transcript",
        "conversation_summary",
        "active_topics",
        "entities",
        "recent_turns",
        "recent_actions",
        "user_language",
    }
    optional = {"mcp_servers", "workspace_dir"}
    assert required <= set(result.keys())
    assert set(result.keys()) - required <= optional
    assert result["persona"] == "Ти Heare."
    assert result["transcript"] == "як справи?"
    # With no conversation_manager, recent_actions is "(none)"
    assert result["recent_actions"] == "(none)"


async def test_build_for_generator_user_language_mapping(store: TranscriptStore) -> None:
    """user_language code is mapped to full language name in build_for_generator output."""
    settings = load_settings()
    ctx = ContextBuilder(store, settings)

    result_uk = await ctx.build_for_generator(transcript="x", persona="p", user_language="uk")
    assert result_uk["user_language"] == "Ukrainian"

    result_en = await ctx.build_for_generator(transcript="x", persona="p", user_language="en")
    assert result_en["user_language"] == "English"

    result_ru = await ctx.build_for_generator(transcript="x", persona="p", user_language="ru")
    assert result_ru["user_language"] == "Russian"

    result_fr = await ctx.build_for_generator(transcript="x", persona="p", user_language="fr")
    assert result_fr["user_language"] == "English"


async def test_build_for_generator_recent_actions_formatting(store: TranscriptStore) -> None:
    """recent_actions reflects ConversationManager._action_log entries."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    mgr.record_action_pending(1, "bash", "echo hi")
    mgr.record_action_result(1, "ran: echo hi")
    mgr.record_action_pending(2, "search", "rates")

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    assert "bash" in formatted
    assert "search" in formatted
    # Glyphs for done + pending
    assert "✓" in formatted
    assert "⋯" in formatted


async def test_build_for_generator_recent_actions_limit(store: TranscriptStore) -> None:
    """Formatter shows at most 5 entries."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    for i in range(1, 9):  # 8 entries
        mgr.record_action_pending(i, "bash", f"cmd{i}")

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    # Count lines
    lines = [ln for ln in formatted.split("\n") if ln.strip().startswith("-")]
    assert len(lines) == 5


async def test_format_recent_actions_keeps_web_search_content(
    store: TranscriptStore,
) -> None:
    """web_search results must survive past the 80-char truncation cap so the
    generator can answer follow-ups from prior search content."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    long_recipe = (
        "Chili Recipe\nBrown beef with onion and garlic, then add chili powder, "
        "cumin, tomatoes, and beans; simmer twenty minutes. " * 8
    )
    assert len(long_recipe) > 80
    mgr.record_action_pending(1, "web_search", "chili recipe")
    mgr.record_action_result(1, long_recipe)

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    assert "Brown beef" in formatted
    # The tail (a single appended entry, possibly multi-line) must hold
    # well more than the 80-char cap that applies to other tools.
    assert formatted.count("twenty minutes") >= 3, (
        f"web_search tail should keep the long recipe; got {len(formatted)} chars"
    )
    assert len(formatted) > 200


async def test_format_recent_actions_truncates_other_tools(
    store: TranscriptStore,
) -> None:
    """Non-web tools stay capped at the 80-char tail to keep prompts tight."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    long_output = "x" * 200
    mgr.record_action_pending(1, "bash", "cat huge")
    mgr.record_action_result(1, long_output)

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    line = next(ln for ln in formatted.splitlines() if "bash" in ln)
    # The tail (the part after `bash: `) must be at most 80 chars.
    tail = line.split("bash: ", 1)[1]
    assert len(tail) <= 80


# -------- CCS-02: items-first rendering for web_search/web_fetch --------

async def test_format_recent_actions_items_first_for_web_search(
    store: TranscriptStore,
) -> None:
    """When a web_search entry has structured `items`, render numbered
    1./2./3. blocks from items, NOT the legacy `result` blob."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    items = [
        {"n": 1, "title": "Recipe One", "url": "https://e.com/1", "snippet": "First recipe."},
        {"n": 2, "title": "Recipe Two", "url": "https://e.com/2", "snippet": "Second recipe."},
        {"n": 3, "title": "Recipe Three", "url": "https://e.com/3", "snippet": "Third recipe."},
    ]
    mgr.record_action_pending(1, "web_search", "chili recipe")
    mgr.record_action_result(1, "summary text not used here", items=items)

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    assert "1. Recipe One" in formatted
    assert "2. Recipe Two" in formatted
    assert "3. Recipe Three" in formatted
    assert "First recipe." in formatted
    assert "https://e.com/1" in formatted


async def test_format_recent_actions_truncates_long_items_tail_first(
    store: TranscriptStore,
) -> None:
    """5 items × ~250-char snippets exceed 1800 chars → tail is dropped
    and a '(N more items truncated)' suffix is appended. Total length
    must remain <= 1800 chars (per AC)."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    # Each item is ~520 chars (440 snippet + title + url + numbering); 5
    # items joined by blank lines = ~2600 chars, well over the 1800 cap.
    snippet = "lorem ipsum dolor sit amet " * 16  # ~432 chars
    items = [
        {"n": i, "title": f"Title {i}", "url": f"https://e.com/{i}", "snippet": snippet}
        for i in range(1, 6)
    ]
    mgr.record_action_pending(1, "web_search", "long query")
    mgr.record_action_result(1, "ignored", items=items)

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    # Extract the tail after "web_search: " — items render as multi-line
    # so we measure from the prefix to end.
    idx = formatted.index("web_search: ") + len("web_search: ")
    tail = formatted[idx:]
    assert len(tail) <= 1800, f"web entry tail must be <=1800 chars, got {len(tail)}"
    assert "more items truncated" in formatted, (
        f"expected truncation suffix, got: {formatted!r}"
    )
    # First item should always survive truncation.
    assert "1. Title 1" in formatted


async def test_format_recent_actions_falls_back_to_result_when_no_items(
    store: TranscriptStore,
) -> None:
    """Entry with `result` only (no `items`) hits the legacy fallback path
    — substring of `result` appears in the output."""
    from src.store.conversation import ConversationManager

    settings = load_settings()
    mgr = ConversationManager(store)
    legacy_blob = "Legacy result blob. Brown beef, simmer twenty minutes."
    mgr.record_action_pending(1, "web_search", "chili recipe")
    mgr.record_action_result(1, legacy_blob)  # no items=

    ctx = ContextBuilder(store, settings, conversation_manager=mgr)
    result = await ctx.build_for_generator(transcript="?", persona="p")
    formatted = result["recent_actions"]
    assert "Legacy result blob." in formatted
    assert "Brown beef" in formatted
    # No numbered "1. " or "(N more items truncated)" since the entry has no items.
    assert "more items truncated" not in formatted


async def test_context_builder_keys_accounted_for(store: TranscriptStore) -> None:
    """Drift guard: every key in build() must be either propagated to the generator
    view or explicitly listed in _EXCLUDED_FROM_GENERATOR_CTX."""
    from src.store.context import _EXCLUDED_FROM_GENERATOR_CTX

    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    full = await ctx.build("x", heartbeat=False)
    gen = await ctx.build_for_generator(transcript="x", persona="p")

    missing_from_gen = set(full.keys()) - set(gen.keys())
    assert missing_from_gen == _EXCLUDED_FROM_GENERATOR_CTX, (
        f"build() keys not in generator view: {missing_from_gen}. "
        f"Expected exactly: {_EXCLUDED_FROM_GENERATOR_CTX}. "
        "If you added a new key to build(), decide whether it should flow into "
        "the generator prompt (add to generator view) or is intentionally "
        "excluded (add to _EXCLUDED_FROM_GENERATOR_CTX)."
    )


# ---------------------------------------------------------------------------
# MCP server injection (architect HIGH fix): post-cutover ContextBuilder
# must surface MCP server descriptions to the system prompt so the LLM
# knows the mcp__<server>__* tools exist.


async def test_build_for_generator_injects_mcp_servers(
    store: TranscriptStore, tmp_path
) -> None:
    """When workspace_dir/.mcp.json defines servers, build_for_generator
    surfaces ``mcp_servers`` in the result dict."""
    import json as _json

    settings = Settings()
    settings.workspace_dir = tmp_path
    (tmp_path / ".mcp.json").write_text(
        _json.dumps(
            {
                "mcpServers": {
                    "memory": {"description": "long-term notes"},
                    "filesystem": {"description": "fs access"},
                }
            }
        )
    )

    ctx = ContextBuilder(store, settings)
    result = await ctx.build_for_generator(transcript="hi", persona="p")
    assert "mcp_servers" in result
    assert "memory" in result["mcp_servers"]
    assert "filesystem" in result["mcp_servers"]
    assert "mcp__memory__*" in result["mcp_servers"]


async def test_build_for_generator_omits_mcp_servers_when_none(
    store: TranscriptStore, tmp_path
) -> None:
    """When no .mcp.json (or empty servers), the key is absent from the
    result so the system-prompt block stays untriggered."""
    settings = Settings()
    settings.workspace_dir = tmp_path  # no .mcp.json file in tmp dir

    ctx = ContextBuilder(store, settings)
    result = await ctx.build_for_generator(transcript="hi", persona="p")
    assert "mcp_servers" not in result


# ---------------------------------------------------------------------------
# T7: Prompt section system — ordering, templates, and rendering
# ---------------------------------------------------------------------------


def test_prompt_sections_count_and_keys() -> None:
    """All registered prompt sections are present with unique keys."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    assert len(PROMPT_SECTIONS) >= 11, (
        f"Expected at least 11 prompt sections, got {len(PROMPT_SECTIONS)}"
    )
    keys = [s.key for s in PROMPT_SECTIONS]
    assert len(keys) == len(set(keys)), f"Duplicate section keys: {keys}"


def test_prompt_sections_sorted_order() -> None:
    """Sections sorted by order must produce a valid sequence with
    no order collisions."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    sorted_sections = sorted(PROMPT_SECTIONS, key=lambda s: s.order)

    # Orders must be strictly increasing (no collisions)
    orders = [s.order for s in sorted_sections]
    for i in range(1, len(orders)):
        assert orders[i] > orders[i - 1], (
            f"Section order not strictly increasing at index {i}: "
            f"{sorted_sections[i-1].key}={orders[i-1]}, "
            f"{sorted_sections[i].key}={orders[i]}"
        )


def test_prompt_sections_template_paths_exist() -> None:
    """Every template_path in PROMPT_SECTIONS points to an existing,
    readable file under the project root."""
    from src.agent.llm.prompt_sections import PROMPT_SECTIONS

    root = Path(__file__).parent.parent
    missing: list[str] = []
    for section in PROMPT_SECTIONS:
        if section.source == "template" and section.template_path:
            full = root / section.template_path
            if not full.is_file():
                missing.append(f"{section.key} -> {section.template_path}")
    assert not missing, (
        f"Template files not found:\n" + "\n".join(missing)
    )


def test_render_prompt_section_ordering() -> None:
    """Rendered prompt respects section order: templated sections appear
    in correct order."""
    from src.agent.llm.prompt_sections import render_prompt

    ctx = {
        "mode": "ambient",
        "mode_block": "MODE BLOCK",
    }
    out = render_prompt(persona="Test", context=ctx, language="en")

    token_positions: dict[str, int] = {}
    markers = [
        ("tool_marker", out.find("Tool-use loop:")),
        ("narration_marker", out.find("Narration during tool use:")),
        ("routing_marker", out.find("Routing \u2014 pick by symptom:")),
        ("reply_marker", out.find("Reply rules:")),
        ("speech_marker", out.find("Speech style:")),
    ]
    for name, idx in markers:
        if idx != -1:
            token_positions[name] = idx

    assert token_positions, "No section markers found in rendered prompt"


def test_full_prompt_contains_all_required_sections() -> None:
    """A full prompt render includes persona, context, reply rules,
    routing, tool use, and speech style sections."""
    from src.agent.llm.prompt_sections import render_prompt

    ctx = {
        "time": "2026-01-01 12:00:00",
        "timezone": "UTC",
        "mode": "ambient",
        "mode_block": "Mode: ambient",
        "recent_transcripts": "(none)",
        "conversation_summary": "No history.",
        "active_topics": "",
        "entities": "",
        "recent_turns": "(none)",
        "recent_actions": "(none)",
        "mcp_servers": "None connected.",
    }
    out = render_prompt(
        persona="I am Heare.", context=ctx, language="en"
    )

    # All required sections must produce content
    required_checks = [
        ("persona", "I am Heare."),
        ("context", "2026-01-01 12:00:00"),
        ("reply_rules", "Reply rules:"),
        ("routing", "Routing \u2014 pick by symptom:"),
        ("tool_use", "Tool-use loop:"),
        ("speech_style", "Speech style:"),
        ("narration", "Narration during tool use:"),
        ("capabilities", "### Capabilities"),
    ]
    for name, token in required_checks:
        assert token in out, (
            f"Section '{name}' not found or token '{token}' missing"
        )
