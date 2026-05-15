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
        "speaker_rule_block",
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
    settings.speaker_id_enabled = False
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("три", heartbeat=False)
    assert "один" in result["recent_transcripts"]
    assert "два" in result["recent_transcripts"]


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


async def test_build_returns_speaker_rule_block_empty_when_flag_off(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    settings.speaker_id_enabled = False
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("x", heartbeat=False)
    assert result["speaker_rule_block"] == ""


async def test_build_returns_speaker_rule_block_when_flag_on(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    settings.speaker_id_enabled = True
    ctx = ContextBuilder(store, settings)
    # No speaker_id → likely owner path (below ID threshold)
    result = await ctx.build("x", heartbeat=False)
    assert "Speaker: likely owner" in result["speaker_rule_block"]
    # owner speaker_id → high confidence path
    result_owner = await ctx.build("x", heartbeat=False, speaker_id="owner")
    assert "Speaker: owner (high confidence)" in result_owner["speaker_rule_block"]


async def test_build_keeps_placeholder_literal_with_keep_placeholders(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(
        "x", heartbeat=False, keep_placeholders=["speaker_rule_block"]
    )
    assert result["speaker_rule_block"] == "{speaker_rule_block}"


async def test_build_keeps_transcript_placeholder_literal(
    store: TranscriptStore,
) -> None:
    settings = load_settings()
    ctx = ContextBuilder(store, settings)
    result = await ctx.build(
        "x", heartbeat=False, keep_placeholders=["transcript_or_heartbeat"]
    )
    assert result["transcript_or_heartbeat"] == "{transcript_or_heartbeat}"


async def test_golden_string_flag_off_render(store: TranscriptStore) -> None:
    """Flag-off rendering must be byte-stable across runs.

    First run captures the rendered output (with a fixed ctx) into
    tests/fixtures/decider_prompt_flag_off.golden.txt. Subsequent runs
    re-render and diff against the golden.
    """
    settings = load_settings()
    settings.speaker_id_enabled = False
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
        "speaker_rule_block": ctx._render_rule_block(),
        "silence_block": "",
        "proactivity_block": "",
    }
    template = (
        Path(__file__).parent.parent.joinpath("prompts", "decider.txt").read_text()
    )
    rendered = ctx.render(template, fixed_ctx)

    golden_path = (
        Path(__file__).parent / "fixtures" / "decider_prompt_flag_off.golden.txt"
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

    # When flag is off, the rendered output must NOT contain the Speaker rule
    assert "Speaker: owner" not in rendered


async def test_format_recent_labels_speakers_when_flag_on(
    store: TranscriptStore,
) -> None:
    await store.log_transcript("я тут", "ambient", speaker_id="owner")
    await store.log_transcript("stranger speak", "ambient", speaker_id="guest_01")
    settings = load_settings()
    settings.speaker_id_enabled = True
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("х", heartbeat=False)
    rendered = result["recent_transcripts"]
    assert "owner: я тут" in rendered
    assert "guest_01: stranger speak" in rendered
    assert "[REDACTED]" not in rendered


async def test_format_recent_passthrough_when_flag_off(
    store: TranscriptStore,
) -> None:
    await store.log_transcript("я тут", "ambient", speaker_id="owner")
    await store.log_transcript("stranger speak", "ambient", speaker_id="unknown")
    settings = load_settings()
    settings.speaker_id_enabled = False
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("х", heartbeat=False)
    rendered = result["recent_transcripts"]
    assert "я тут" in rendered
    assert "stranger speak" in rendered
    assert "owner:" not in rendered  # no speaker labels when flag is off
    assert "[REDACTED]" not in rendered


async def test_format_recent_none_speaker_id_marked_unknown(
    store: TranscriptStore,
) -> None:
    await store.log_transcript("legacy row", "ambient", speaker_id=None)
    settings = load_settings()
    settings.speaker_id_enabled = True
    ctx = ContextBuilder(store, settings)
    result = await ctx.build("х", heartbeat=False)
    rendered = result["recent_transcripts"]
    assert "unknown: legacy row" in rendered
    assert "[REDACTED]" not in rendered


async def test_format_recent_uses_gallery_labels(
    store: TranscriptStore,
) -> None:
    from unittest.mock import MagicMock

    await store.log_transcript("hello", "ambient", speaker_id="guest_01")
    settings = load_settings()
    settings.speaker_id_enabled = True
    gallery = MagicMock()
    gallery.get_label = MagicMock(return_value="Alice")
    ctx = ContextBuilder(store, settings, speaker_gallery=gallery)
    result = await ctx.build("х", heartbeat=False)
    rendered = result["recent_transcripts"]
    assert "Alice: hello" in rendered
    gallery.get_label.assert_called_with("guest_01")


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
